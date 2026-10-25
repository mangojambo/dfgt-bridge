# SPDX-License-Identifier: GPL-2.0-only
"""Mapeamento puro sobre o relatório preservado; nenhuma operação de dispositivo."""
import copy

from .virtual_g29 import BUTTON_CODES

# Índices do campo Button 1..25, sem alterar o descritor ou deslocar bits.
BUTTON_NAMES = ('X', 'Quadrado', 'Círculo', 'Triângulo', 'R1', 'L1', 'R2', 'L2',
                'Share', 'Options', 'R3', 'L3', 'Marcha 1', 'Marcha 2', 'Marcha 3',
                'Marcha 4', 'Marcha 5', 'Marcha 6', 'Ré', '+', '−',
                'Rotary Right', 'Rotary Left', 'Enter', 'PS')
TARGET_NAMES = {f'button:{i+1}': name for i, name in enumerate(BUTTON_NAMES)}
TARGET_NAMES.update({'hat:up': 'D-pad Up', 'hat:down': 'D-pad Down',
                     'hat:left': 'D-pad Left', 'hat:right': 'D-pad Right'})
PHYSICAL_NAMES = {288: 'X', 289: 'Quadrado', 290: 'Círculo', 291: 'Triângulo',
    292: 'Borboleta direita', 293: 'Borboleta esquerda', 294: 'R2', 295: 'L2',
    296: 'Select / menu', 297: 'Start / menu', 298: 'R3', 299: 'L3',
    300: 'Manche baixo / comando do corpo (código compartilhado)',
    301: 'Manche cima / comando do corpo (código compartilhado)',
    302: 'Clique do dial', 303: '+', 704: 'Dial: sentido A (direita a confirmar)',
    705: 'Dial: sentido B (esquerda a confirmar)', 706: '−', 707: 'Buzina GT', 708: 'Guide / central'}
AXES = {'steering': (0, 16383), 'accelerator': (1, 255), 'brake': (2, 255)}
PRESETS = {'g29': 'G29 padrão', 'gears': 'Marchas prontas', 'custom': 'Personalizado'}


def source_name(source):
    if source.startswith('key:'):
        code = int(source[4:])
        symbol = ('BTN_TRIGGER', 'BTN_THUMB', 'BTN_THUMB2', 'BTN_TOP', 'BTN_TOP2',
                  'BTN_PINKIE', 'BTN_BASE', 'BTN_BASE2', 'BTN_BASE3', 'BTN_BASE4',
                  'BTN_BASE5', 'BTN_BASE6')[code-288] if 288 <= code <= 299 else (
                  f'BTN_TRIGGER_HAPPY{code-703}' if 704 <= code <= 743 else 'EV_KEY')
        return f'{code} · {symbol} · {PHYSICAL_NAMES.get(code, "Entrada a identificar")}'
    return f'ABS_HAT0 · {TARGET_NAMES.get(source, source)}'


def validate_mapping(value):
    if not isinstance(value, dict) or set(value) != {'bindings', 'axes'}:
        raise ValueError('Mapeamento precisa de bindings e axes')
    if not isinstance(value['bindings'], dict) or len(value['bindings']) > 772:
        raise ValueError('Lista de entradas inválida')
    for source, target in value['bindings'].items():
        if not isinstance(source, str) or not isinstance(target, str):
            raise ValueError('Entrada e destino precisam ser identificadores de texto')
        valid = source in ('hat:up', 'hat:down', 'hat:left', 'hat:right')
        if isinstance(source, str) and source.startswith('key:'):
            try:
                code = int(source[4:])
                valid = 0 <= code < 768 and source == f'key:{code}'
            except ValueError:
                pass
        if not valid or target not in TARGET_NAMES:
            raise ValueError(f'Entrada/destino inválido: {source} → {target}')
    if not isinstance(value['axes'], dict) or set(value['axes']) != set(AXES):
        raise ValueError('Eixos incompletos')
    for axis in value['axes'].values():
        if (not isinstance(axis, dict) or set(axis) != {'source', 'invert'} or
                type(axis['source']) is not int or axis['source'] not in (0, 1, 2) or
                type(axis['invert']) is not bool):
            raise ValueError('Eixo deve selecionar ABS_X/Y/Z e inversão booleana')
    return copy.deepcopy(value)


def preset(name='g29', *, down='button:2', up='button:1'):
    if name not in PRESETS or down not in TARGET_NAMES or up not in TARGET_NAMES:
        raise ValueError('Preset ou destino de marchas inválido')
    # Preservar também os slots adicionais legados sem inventar sua origem física.
    bindings = {f'key:{code}': f'button:{i+1}' for i, code in enumerate(BUTTON_CODES)}
    bindings.update({'key:301': 'button:6', 'key:300': 'button:5',
        'key:303': 'hat:up', 'key:706': 'hat:down', 'key:707': 'button:20',
        'key:302': 'button:24', 'key:704': 'button:22', 'key:705': 'button:23',
        'key:708': 'button:25'})
    bindings.update({s: s for s in ('hat:up', 'hat:down', 'hat:left', 'hat:right')})
    if name == 'gears':
        for code in (293, 301):
            bindings[f'key:{code}'] = down
        for code in (292, 300):
            bindings[f'key:{code}'] = up
    return dict(bindings=bindings, axes={name: dict(source=code, invert=False) for name, (code, _) in AXES.items()})


def active_sources(state):
    result = {f'key:{code}' for code in state[5]}
    hx, hy = state[3:5]
    result.update(s for s, active in (('hat:left', hx < 0), ('hat:right', hx > 0),
                                     ('hat:up', hy < 0), ('hat:down', hy > 0)) if active)
    return result


def map_state(state, mapping):
    # Mapping já validado na fronteira de configuração, fora do hot path.
    targets = {mapping['bindings'][s] for s in active_sources(state) if s in mapping['bindings']}
    values = []
    for name, (_, maximum) in AXES.items():
        axis = mapping['axes'][name]
        source_max = 16383 if axis['source'] == 0 else 255
        value = max(0, min(source_max, state[axis['source']]))
        if axis['invert']:
            value = source_max-value
        values.append(round(value*maximum/source_max))
    return (*values, int('hat:right' in targets)-int('hat:left' in targets),
            int('hat:down' in targets)-int('hat:up' in targets),
            sorted(BUTTON_CODES[int(t[7:])-1] for t in targets if t.startswith('button:')))


def warnings(mapping):
    result = ['EV_KEY 300/301: manche e comandos do corpo podem compartilhar códigos; não são distinguíveis.',
              'Dial 704/705: confirme o sentido físico em Detectar botão; os destinos podem ser trocados.']
    by_target = {}
    for source, target in mapping['bindings'].items():
        by_target.setdefault(target, []).append(source)
    for target, sources in by_target.items():
        if len(sources) > 1:
            result.append(f'{TARGET_NAMES[target]}: estado agregado de {", ".join(sources)} (OR).')
    by_axis = {}
    for target, axis in mapping['axes'].items():
        by_axis.setdefault(axis['source'], []).append(target)
    result.extend(f'ABS_{"XYZ"[source]} alimenta vários eixos: {", ".join(names)}'
                  for source, names in by_axis.items() if len(names) > 1)
    return result
