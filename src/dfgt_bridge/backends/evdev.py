# SPDX-License-Identifier: GPL-2.0-only
"""Único slot FF_CONSTANT DFGT; só abre hardware com enable_ffb=True.

ABI Linux x86_64: input.h/ff_effect. Sem python-evdev, USB ou comandos vendor.
"""
import ctypes as C
import fcntl
import os
import platform
import struct
from types import SimpleNamespace

from ..input.physical_wheel import INPUT_EVENT


class Envelope(C.Structure):
    _fields_ = [(k, C.c_uint16) for k in ('attack_length', 'attack_level', 'fade_length', 'fade_level')]


class Constant(C.Structure):
    _fields_ = [('level', C.c_int16), ('envelope', Envelope)]


class Periodic(C.Structure):
    _fields_ = [('waveform', C.c_uint16), ('period', C.c_uint16), ('magnitude', C.c_int16),
                ('offset', C.c_int16), ('phase', C.c_uint16), ('envelope', Envelope),
                ('custom_len', C.c_uint32), ('custom_data', C.POINTER(C.c_int16))]


class EffectUnion(C.Union):
    _fields_ = [('constant', Constant), ('periodic', Periodic)]


class Pair(C.Structure):
    _fields_ = [('first', C.c_uint16), ('second', C.c_uint16)]


class Effect(C.Structure):
    _fields_ = [('type', C.c_uint16), ('id', C.c_int16), ('direction', C.c_uint16),
                ('trigger', Pair), ('replay', Pair), ('u', EffectUnion)]


class EvdevBackend:
    def __init__(self, path, *, enable_ffb=False):
        if enable_ffb is not True:
            raise PermissionError('Backend físico exige --enable-ffb')
        if platform.machine() != 'x86_64' or C.sizeof(Effect) != 48 or INPUT_EVENT.size != 24:
            raise RuntimeError('ABI EVDEV física suportada: Linux x86_64, ff_effect=48')
        self.fd = os.open(path, os.O_RDWR | os.O_NONBLOCK | os.O_CLOEXEC)
        self.slot = -1
        self.validated = self.gain_on = False
        self.freshness_guard = lambda: None
        self.uploads = self.deduplicated = 0
        self.signature = None
        try:
            _, vendor, product, _ = struct.unpack('=4H', fcntl.ioctl(self.fd, 0x80084502, bytes(8)))
            if (vendor, product) != (0x046d, 0xc29a):
                raise ValueError('Saída física aceita somente DFGT 046d:c29a; G920 proibido')
            self.info = SimpleNamespace(vendor=vendor, product=product)
            bitmap = fcntl.ioctl(self.fd, 0x80104535, bytes(16))  # EVIOCGBIT(EV_FF,16)
            self.features = [i for i in range(128) if bitmap[i//8] & (1 << (i % 8))]
            if not {0x52, 0x60, 0x61}.issubset(self.features):
                raise ValueError('DFGT deve anunciar FF_CONSTANT, FF_GAIN e FF_AUTOCENTER')
            self.capacity = struct.unpack('=I', fcntl.ioctl(self.fd, 0x80044584, bytes(4)))[0]
            if self.capacity < 1:
                raise ValueError('Nenhum slot EVDEV disponível')
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)  # Cooperativo; não bloqueia Steam.
            self.validated = True
            self._event(0x15, 0x60, 0)
            self._event(0x15, 0x61, 0)
            self.syn()
        except BaseException:
            self.close()
            raise

    def capabilities(self):
        return {0x15: self.features}

    def _event(self, kind, code, value):
        data = INPUT_EVENT.pack(0, 0, kind, code, value)
        if os.write(self.fd, data) != len(data):
            raise OSError('Escrita EVDEV parcial; abortar sem reenviar cauda')

    def upload_effect(self, request):
        if (request.effect_id not in (-1, self.slot) or not 1 <= request.duration_ms <= 160
                or request.direction != 0x4000 or not -32767 <= request.level <= 32767):
            raise ValueError('Pedido físico fora do contrato Constant/160ms')
        self.freshness_guard()
        signature = request.level, request.duration_ms, request.direction
        if self.slot >= 0 and signature == self.signature:
            self.deduplicated += 1
            return self.slot
        effect = Effect()
        effect.type, effect.id, effect.direction = 0x52, self.slot, 0x4000
        effect.replay.first = request.duration_ms
        effect.u.constant.level = request.level
        buffer = bytearray(bytes(effect))
        fcntl.ioctl(self.fd, 0x40304580, buffer, True)  # EVIOCSFF
        allocated = Effect.from_buffer_copy(buffer).id
        if not 0 <= allocated < self.capacity or (self.slot >= 0 and allocated != self.slot):
            raise OSError('Kernel não reutilizou o único slot físico')
        self.slot, self.signature = allocated, signature
        self.uploads += 1
        self.freshness_guard()  # Upload lento não autoriza um play vencido.
        return self.slot

    def write(self, kind, code, value):
        if kind != 0x15 or code != self.slot or self.slot < 0 or value not in (0, 1):
            raise ValueError('Somente play/stop do slot próprio')
        if value:
            self.freshness_guard()
            if not self.gain_on:
                self._event(0x15, 0x60, 65535)
                self.gain_on = True
            self.freshness_guard()
        else:
            self._event(0x15, 0x60, 0)
            self.gain_on = False
        self._event(kind, code, value)

    def syn(self):
        self._event(0, 0, 0)

    def erase_effect(self, effect_id):
        if effect_id != self.slot or effect_id < 0:
            raise ValueError('Slot físico não pertence a este backend')
        fcntl.ioctl(self.fd, 0x40044581, effect_id)  # EVIOCRMFF recebe ID, não ponteiro.
        self.slot, self.signature = -1, None

    def close(self):
        if self.fd < 0:
            return
        try:
            if self.validated:
                # Tentar todas as proteções mesmo se uma delas falhar/desconectar.
                operations = [lambda: self._event(0x15, 0x60, 0)]
                if self.slot >= 0:
                    operations += [lambda: self._event(0x15, self.slot, 0),
                                   lambda: self.erase_effect(self.slot)]
                for operation in operations:
                    try:
                        operation()
                    except OSError:
                        pass
        finally:
            os.close(self.fd)  # Kernel libera efeitos deste fd mesmo se erase falhar.
            self.fd = -1

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
