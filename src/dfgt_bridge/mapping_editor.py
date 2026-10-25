# SPDX-License-Identifier: GPL-2.0-only
"""Estado da GUI testável sem Tk/display/dispositivos; prévia usa o mapper real."""
import copy
import struct

from .config import default_profile, validate_profile
from .input.mapping import active_sources, map_state, preset, TARGET_NAMES, warnings, source_name
from .input.virtual_g29 import report, BUTTON_CODES


class MappingConflict(ValueError):
    pass


class MappingEditor:
    def __init__(self, profile=None):
        self.profile = validate_profile(profile or default_profile())
        self.history, self.future = [], []
        self.selected = 'button:6'
        self.state = (8192, 255, 255, 0, 0, [])
        self.detecting = False
        self.detected = None
        self.notice = ''

    def commit(self, value):
        value = validate_profile(value)
        if value == self.profile:
            return
        self.history.append(copy.deepcopy(self.profile))
        self.history = self.history[-50:]
        self.future.clear()
        self.profile = value

    def select(self, target):
        if target not in TARGET_NAMES:
            raise ValueError('Destino inválido')
        self.selected = target
        return self.sources()

    def sources(self):
        return sorted(s for s, target in self.profile['mapping']['bindings'].items() if target == self.selected)

    def bind(self, source, target=None, *, replace=False):
        target = target or self.selected
        if target not in TARGET_NAMES:
            raise ValueError('Destino inválido')
        old = self.profile['mapping']['bindings'].get(source)
        if old is not None and old != target and not replace:
            raise MappingConflict(f'{source_name(source)} já envia {TARGET_NAMES[old]}. Mover para {TARGET_NAMES[target]}?')
        value = copy.deepcopy(self.profile)
        value['mapping']['bindings'][source] = target
        value['preset'] = 'custom'
        self.commit(value)

    def remove(self, source):
        value = copy.deepcopy(self.profile)
        value['mapping']['bindings'].pop(source, None)
        value['preset'] = 'custom'
        self.commit(value)

    def apply_preset(self, name, **kwargs):
        value = copy.deepcopy(self.profile)
        if name != 'custom':
            value['mapping'] = preset(name, **kwargs)
        value['preset'] = name
        self.commit(value)
        self.notice = ('Marchas prontas reutiliza botões virtuais: confira conflitos de menu e configure as marchas no jogo.'
                       if name == 'gears' else '')

    def undo(self):
        if self.history:
            self.future.append(self.profile)
            self.profile = self.history.pop()

    def swap_dial(self):
        value = copy.deepcopy(self.profile)
        bindings = value['mapping']['bindings']
        a, b = bindings.get('key:704'), bindings.get('key:705')
        if a is None or b is None:
            raise ValueError('Associe primeiro os dois sentidos 704/705')
        bindings['key:704'], bindings['key:705'] = b, a
        value['preset'] = 'custom'
        self.commit(value)

    def redo(self):
        if self.future:
            self.history.append(self.profile)
            self.profile = self.future.pop()

    def detect(self):
        self.detecting = True
        self.detected = None

    def observe(self, state, events=()):
        previous = active_sources(self.state)
        self.state = tuple(copy.deepcopy(state))
        if self.detecting:
            candidates = [f'key:{code}' for kind, code, value in events if kind == 1 and value == 1 and 0 <= code < 768]
            candidates.extend(sorted(active_sources(state)-previous))
            if candidates:
                self.detected = candidates[0]
                self.detecting = False
                self.notice = 'Detectado: '+source_name(self.detected)
        return self.preview()

    def preview(self):
        mapped = map_state(self.state, self.profile['mapping'])
        virtual = [f'button:{BUTTON_CODES.index(code)+1}' for code in mapped[5]]
        virtual.extend(s for s in active_sources(mapped) if s.startswith('hat:'))
        packet = report(mapped, self.profile['gas_axis'], self.profile['invert_gas'])
        hid_axes = dict(zip(('X', 'Z', 'Rz', 'Y'), struct.unpack('<HBBBBI', packet[1:])[:4]))
        return dict(physical=sorted(active_sources(self.state)), virtual=sorted(virtual),
                    state=mapped, physical_axes=self.state[:3], hid_axes=hid_axes, report=packet.hex(),
                    warnings=warnings(self.profile['mapping']))
