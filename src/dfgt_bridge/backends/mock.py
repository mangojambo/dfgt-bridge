# SPDX-License-Identifier: GPL-2.0-only
"""Backend determinístico que registra comandos sem acessar dispositivos."""
from types import SimpleNamespace
import math
import time


class MockBackend:
    info = SimpleNamespace(vendor=0x046d, product=0xc29a)

    def __init__(self, clock=time.monotonic, max_actions=4096):
        if type(max_actions) is not int or max_actions < 1:
            raise ValueError('Histórico deve ter limite positivo')
        self.clock, self.max_actions = clock, max_actions
        self.actions = []
        self.dropped_actions = 0
        self.effect = None
        self.active_until = None

    def _record(self, action):
        if len(self.actions) == self.max_actions:
            del self.actions[0]
            self.dropped_actions += 1
        self.actions.append(action)

    @property
    def playing(self):
        if self.active_until is None:
            return False
        now = self.clock()
        return math.isfinite(now) and now < self.active_until

    @property
    def level(self):
        return self.effect.level if self.effect is not None and self.playing else 0

    def capabilities(self):
        return {0x15: [0x52]}

    def upload_effect(self, effect):
        if effect.effect_id not in (-1, 7) or not 1 <= effect.duration_ms <= 160:
            raise ValueError('Pedido inválido para o slot mock')
        self.effect = effect
        self._record(('upload', effect))
        return 7

    def write(self, *args):
        if len(args) != 3 or args[:2] != (0x15, 7) or args[2] not in (0, 1):
            raise ValueError('Operação inválida no mock')
        if args[2]:
            if self.effect is None:
                raise ValueError('Play sem efeito carregado')
            now = self.clock()
            if not math.isfinite(now):
                raise ValueError('Relógio mock inválido')
            self.active_until = now + self.effect.duration_ms/1000
        else:
            self.active_until = None
        self._record(('write', args))

    def syn(self):
        self._record(('syn',))

    def erase_effect(self, effect_id):
        if effect_id != 7:
            raise ValueError('Slot mock inexistente')
        self.effect = None
        self.active_until = None
        self._record(('erase', effect_id))
