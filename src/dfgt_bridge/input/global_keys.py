# SPDX-License-Identifier: GPL-2.0-only
"""Teclados EVDEV readonly sem grab; somente no processo físico opt-in."""
import fcntl
import os
from pathlib import Path

from .physical_wheel import INPUT_EVENT


class GlobalKeys:
    def __init__(self, *, enable_ffb=False, path=None):
        if enable_ffb is not True:
            raise PermissionError('Teclas globais físicas exigem --enable-ffb')
        self.devices = {}
        self.errors = []
        candidates = [Path(path)] if path else sorted(set(
            Path('/dev/input/by-id').glob('*-event-kbd')) | set(
            Path('/dev/input/by-path').glob('*-event-kbd')))
        identities = set()
        for candidate in candidates:
            fd = -1
            try:
                fd = os.open(str(candidate), os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
                identity = os.fstat(fd).st_rdev
                keys = fcntl.ioctl(fd, 0x80604521, bytes(96))
                if identity in identities or not all(keys[k//8] & (1 << (k % 8)) for k in (28, 30, 65, 66, 67, 68)):
                    os.close(fd)
                    continue
                identities.add(identity)
                self.devices[fd] = str(candidate)
            except OSError as exc:
                if fd >= 0:
                    os.close(fd)
                self.errors.append(str(exc))

    def read(self):
        pressed = []
        for fd in list(self.devices):
            try:
                data = os.read(fd, INPUT_EVENT.size*64)
                if not data or len(data) % INPUT_EVENT.size:
                    raise OSError('Teclado removido ou evento parcial')
                for _, _, kind, code, value in INPUT_EVENT.iter_unpack(data):
                    if kind == 0 and code == 3:  # Não continuar após perda de tecla de emergência.
                        pressed.append(67)
                    elif kind == 1 and code in (65, 66, 67, 68) and value == 1:
                        pressed.append(code)
            except BlockingIOError:
                pass
            except OSError as exc:
                self.errors.append(str(exc))
                self.errors = self.errors[-16:]
                os.close(fd)
                del self.devices[fd]
                pressed.append(67)
        return pressed

    def close(self):
        for fd in self.devices:
            os.close(fd)
        self.devices.clear()
