# SPDX-License-Identifier: GPL-2.0-only
"""I/O apenas do HID virtual. Cada escrita contém um evento UHID completo."""
import os
import select
import struct
import time

from .ffb.transport import UHID_EVENT_SIZE
from .input.virtual_g29 import create_event


class VirtualDevice:
    def __init__(self):
        self.fd = os.open('/dev/uhid', os.O_RDWR | os.O_CLOEXEC | os.O_NONBLOCK)
        self.created = False
        try:
            self.write(create_event())
            self.created = True
        except BaseException:
            self.close()
            raise

    def write(self, event):
        deadline = time.monotonic() + .25
        while True:
            try:
                count = os.write(self.fd, event)
                break
            except BlockingIOError:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('UHID não aceitou resposta em 250 ms')
                select.select([], [self.fd], [], min(.01, remaining))
        if count != len(event):
            raise OSError('Escrita UHID parcial; não reenviar comando nem cauda')

    def read(self):
        try:
            event = os.read(self.fd, UHID_EVENT_SIZE)
        except BlockingIOError:
            return None
        if not event:
            raise EOFError('UHID encerrado')
        return event

    def close(self):
        if self.fd >= 0:
            try:
                if self.created:
                    self.write(struct.pack('=I', 1))
            finally:
                os.close(self.fd)  # Fechar o fd também destrói UHID se DESTROY falhar.
                self.fd = -1
                self.created = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
