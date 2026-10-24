# SPDX-License-Identifier: GPL-2.0-only
"""Backend determinístico que registra comandos sem acessar dispositivos."""
from types import SimpleNamespace


class MockBackend:
    info = SimpleNamespace(vendor=0x046d, product=0xc29a)

    def __init__(self):
        self.actions = []

    def capabilities(self):
        return {0x15: [0x52]}

    def upload_effect(self, effect):
        self.actions.append(('upload', effect))
        return 7

    def write(self, *args):
        self.actions.append(('write', args))

    def syn(self):
        self.actions.append(('syn',))

    def erase_effect(self, effect_id):
        self.actions.append(('erase', effect_id))
