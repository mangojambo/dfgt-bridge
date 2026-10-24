# SPDX-License-Identifier: GPL-2.0-only
"""Pool virtual determinístico. IDs nunca ultrapassam o descritor preservado."""
from dataclasses import dataclass


DESCRIPTOR_CAPACITY = 40
SUPPORTED_KINDS = frozenset((1, 2, 4, 5, 6, 7, 8, 9, 10, 11, 12))


@dataclass(frozen=True)
class Allocation:
    index: int
    status: int  # 1 Success, 2 Full, 3 Error; índice 0 quando inválido.


class EffectPool:
    """Orçamento lógico fixo por slot; não é medição de RAM Python/firmware."""
    def __init__(self, capacity=40, slot_bytes=64):
        if type(capacity) is not int or not 1 <= capacity <= DESCRIPTOR_CAPACITY:
            raise ValueError('Capacidade deve estar em 1..40')
        if type(slot_bytes) is not int or not 1 <= slot_bytes <= 65535//capacity:
            raise ValueError('Orçamento do pool deve caber em u16')
        self.capacity = capacity
        self.slot_bytes = slot_bytes
        self.effects = {}
        self.last = Allocation(0, 3)

    @property
    def ram_size(self):
        return self.capacity * self.slot_bytes

    @property
    def available(self):
        return (self.capacity-len(self.effects)) * self.slot_bytes

    def allocate(self, kind, factory):
        if kind not in SUPPORTED_KINDS:
            self.last = Allocation(0, 3)
        elif len(self.effects) == self.capacity:
            self.last = Allocation(0, 2)
        else:
            index = next(i for i in range(1, self.capacity+1) if i not in self.effects)
            self.effects[index] = factory(kind)
            self.last = Allocation(index, 1)
        return self.last

    def free(self, index):
        if not 1 <= index <= self.capacity or index not in self.effects:
            raise ValueError('Bloco inexistente')
        del self.effects[index]
        if self.last.index == index:
            self.last = Allocation(0, 3)

    def reset(self):
        self.effects.clear()
        self.last = Allocation(0, 3)
