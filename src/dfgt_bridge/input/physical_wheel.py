# SPDX-License-Identifier: GPL-2.0-only
"""Leitor evdev O_RDONLY: nenhuma operação EV_FF ou escrita no DFGT."""
import fcntl
import os
import struct
import time

SOURCE = '/dev/input/by-id/usb-046d_Driving_Force_GT-event-joystick'
INPUT_EVENT = struct.Struct('@llHHi')


class PhysicalWheel:
    def __init__(self, path=SOURCE):
        self.fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
        try:
            identity = struct.unpack('=HHHH', fcntl.ioctl(self.fd, 0x80084502, bytes(8)))
            if identity[1:3] != (0x046d, 0xc29a):
                raise ValueError('A entrada deve ser DFGT 046d:c29a')
            # EVIOCSCLOCKID afeta somente este leitor; não escreve forças nem
            # muda configuração global. Derivadas usam SYN_REPORT monotônico.
            fcntl.ioctl(self.fd, 0x400445a0, struct.pack('=i', time.CLOCK_MONOTONIC))
            axes = [self.absinfo(code) for code in (0, 1, 2)]
            if [a[1:3] for a in axes] != [(0, 16383), (0, 255), (0, 255)]:
                raise ValueError('Ranges diferentes da baseline v0.8')
            self.values = [a[0] for a in axes] + [0, 0]
            self.pressed = set()
            self.dirty = self.dropped = False
            self.resyncs = 0
            self.frame_times_ns = []
            self.observed_events = []
        except BaseException:
            self.close()
            raise

    def absinfo(self, code):
        return struct.unpack('=6i', fcntl.ioctl(self.fd, 0x80184540 + code, bytes(24)))

    def state(self):
        return (*self.values, sorted(self.pressed))

    def resync(self):
        self.values = [self.absinfo(code)[0] for code in (0, 1, 2, 16, 17)]
        keys = fcntl.ioctl(self.fd, 0x80604518, bytes(96))  # EVIOCGKEY(KEY_CNT/8)
        self.pressed = {code for code in range(768) if keys[code//8] & (1 << (code % 8))}
        self.resyncs += 1

    def read_frames(self):
        """Um lote limitado; SYN_DROPPED exige descartar e consultar estado."""
        self.frame_times_ns = []
        self.observed_events = []
        try:
            data = os.read(self.fd, INPUT_EVENT.size * 64)
        except BlockingIOError:
            return []
        if not data:
            raise EOFError('DFGT desconectado')
        if len(data) % INPUT_EVENT.size:
            raise OSError('input_event parcial')
        frames = []
        for seconds, micros, kind, code, value in INPUT_EVENT.iter_unpack(data):
            if kind == 0 and code == 3:
                self.dropped = True
                continue
            if self.dropped:
                if kind == 0 and code == 0:
                    self.resync()
                    self.dropped = self.dirty = False
                    frames.append(self.state())
                    self.frame_times_ns.append(seconds * 1_000_000_000 + micros * 1000)
                continue
            if kind == 3 and code in (0, 1, 2, 16, 17):
                self.observed_events.append((kind, code, value))
                self.values[(0, 1, 2, 16, 17).index(code)] = value
                self.dirty = True
            elif kind == 1 and value in (0, 1):
                self.observed_events.append((kind, code, value))
                if value:
                    self.pressed.add(code)
                else:
                    self.pressed.discard(code)
                self.dirty = True
            elif kind == 0 and code == 0 and self.dirty:
                frames.append(self.state())
                self.frame_times_ns.append(seconds * 1_000_000_000 + micros * 1000)
                self.dirty = False
        return frames

    def close(self):
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
