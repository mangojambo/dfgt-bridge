#!/usr/bin/env python3
"""DFGT -> controle Xbox 360 virtual (sem capturar exclusivamente o volante)."""
import argparse
import json
from pathlib import Path
from evdev import InputDevice, UInput, AbsInfo, ecodes as e

DFGT = '/dev/input/by-id/usb-046d_Driving_Force_GT-event-joystick'
CONFIG = Path.home() / 'dfgt-bridge' / 'shifter.json'

# Código físico -> botão Xbox; várias teclas podem compartilhar uma saída.
BUTTON_MAP = {
    288: e.BTN_SOUTH,   # X (PlayStation) -> A
    290: e.BTN_EAST,    # O -> B
    289: e.BTN_WEST,    # quadrado -> X
    291: e.BTN_NORTH,   # triangulo -> Y
    293: e.BTN_TL,      # borboleta esquerda -> LB
    292: e.BTN_TR,      # borboleta direita -> RB
    300: e.BTN_TL,      # LB físico
    301: e.BTN_TR,      # RB físico
    299: e.BTN_THUMBL,  # L3
    298: e.BTN_THUMBR,  # R3
    297: e.BTN_START,
    296: e.BTN_SELECT,
    708: e.BTN_MODE,    # Guide
    302: e.BTN_NORTH,   # clique da rodinha -> Y (camera)
    # 707 (buzina) reservado: sem mapeamento por enquanto.
}

# Código físico -> movimento de analógico simulado (valor negativo / positivo)
ANALOG_BUTTONS = {
    e.ABS_Y:  (303, 706),  # + / - => analógico esquerdo cima / baixo
    e.ABS_RX: (295, 294),  # L2 / R2 => analógico direito esq. / dir.
    e.ABS_RY: (705, 704),  # rodinha cima / baixo => analógico direito
}

AXES = {
    e.ABS_X: AbsInfo(0, -32768, 32767, 0, 0, 0),
    e.ABS_Y: AbsInfo(0, -32768, 32767, 0, 0, 0),
    e.ABS_RX: AbsInfo(0, -32768, 32767, 0, 0, 0),
    e.ABS_RY: AbsInfo(0, -32768, 32767, 0, 0, 0),
    e.ABS_Z: AbsInfo(0, 0, 255, 0, 0, 0),
    e.ABS_RZ: AbsInfo(0, 0, 255, 0, 0, 0),
    e.ABS_HAT0X: AbsInfo(0, -1, 1, 0, 0, 0),
    e.ABS_HAT0Y: AbsInfo(0, -1, 1, 0, 0, 0),
}
BUTTONS = sorted(set(BUTTON_MAP.values()))


def scale(value, info, minimum, maximum, invert=False):
    size = info.max - info.min
    fraction = min(1.0, max(0.0, (value - info.min) / size)) if size else 0.0
    if invert:
        fraction = 1.0 - fraction
    return round(minimum + fraction * (maximum - minimum))


def next_button(device, prompt):
    print(prompt, flush=True)
    for event in device.read_loop():
        if event.type == e.EV_KEY and event.value == 1:
            print(f'  Código identificado: {event.code}', flush=True)
            return event.code
    raise RuntimeError('Leitura do manche interrompida')


def learn_shifter():
    device = InputDevice(DFGT)
    try:
        print('Aprendizado do manche; pressione APENAS os movimentos solicitados.')
        up = next_button(device, '1. Mova o manche para CIMA uma vez:')
        down = next_button(device, '2. Mova o manche para BAIXO uma vez:')
        if up == down:
            raise ValueError('Os dois movimentos retornaram o mesmo código.')
        forbidden = set(BUTTON_MAP) | {code for pair in ANALOG_BUTTONS.values() for code in pair}
        if up in forbidden or down in forbidden:
            raise ValueError('Um dos códigos já pertence a outro botão. Não salvei o mapeamento.')
        CONFIG.parent.mkdir(parents=True, exist_ok=True)
        CONFIG.write_text(json.dumps({'up': up, 'down': down}, indent=2) + '\n')
        print(f'Manche configurado: cima {up} -> LB; baixo {down} -> RB.')
        print(f'Configuração salva em {CONFIG}')
    finally:
        device.close()


def main():
    mapping = dict(BUTTON_MAP)
    if CONFIG.exists():
        shifter = json.loads(CONFIG.read_text())
        mapping[int(shifter['up'])] = e.BTN_TL
        mapping[int(shifter['down'])] = e.BTN_TR
        print(f"Manche: cima={shifter['up']} (LB), baixo={shifter['down']} (RB)")
    else:
        print('Manche pendente: rode --learn-shifter para identificar os códigos.')

    sources = {}
    for physical, virtual in mapping.items():
        sources.setdefault(virtual, set()).add(physical)

    physical = InputDevice(DFGT)
    try:
        steering = physical.absinfo(e.ABS_X)
        accelerator = physical.absinfo(e.ABS_Y)
        brake = physical.absinfo(e.ABS_Z)
        print(f'DFGT físico: {physical.name}')
        with UInput(
            {e.EV_KEY: BUTTONS, e.EV_ABS: AXES},
            name='Microsoft X-Box 360 pad',
            vendor=0x045e,
            product=0x028e,
            version=0x0110,
            bustype=e.BUS_USB,
        ) as virtual:
            print(f'Xbox virtual: {virtual.device.path}')
            print('Ctrl+C encerra e remove o Xbox virtual.')
            # Inicializar ambos os gatilhos como soltos: LT=freio; RT=acelerador.
            initial = {
                e.ABS_X: scale(steering.value, steering, -32768, 32767),
                e.ABS_Y: 0, e.ABS_RX: 0, e.ABS_RY: 0,
                e.ABS_Z: scale(brake.value, brake, 0, 255, invert=True),
                e.ABS_RZ: scale(accelerator.value, accelerator, 0, 255, invert=True),
                e.ABS_HAT0X: 0, e.ABS_HAT0Y: 0,
            }
            for code, value in initial.items():
                virtual.write(e.EV_ABS, code, value)
            virtual.syn()
            pressed = set()
            virtual_button_state = {}
            virtual_axis_state = {code: 0 for code in ANALOG_BUTTONS}
            for event in physical.read_loop():
                if event.type == e.EV_ABS:
                    if event.code == e.ABS_X:
                        dest, val = e.ABS_X, scale(event.value, steering, -32768, 32767)
                    elif event.code == e.ABS_Y:
                        dest, val = e.ABS_RZ, scale(event.value, accelerator, 0, 255, invert=True)
                    elif event.code == e.ABS_Z:
                        dest, val = e.ABS_Z, scale(event.value, brake, 0, 255, invert=True)
                    elif event.code in (e.ABS_HAT0X, e.ABS_HAT0Y):
                        dest, val = event.code, event.value  # D-pad inalterado
                    else:
                        continue
                    virtual.write(e.EV_ABS, dest, val)
                    virtual.syn()
                elif event.type == e.EV_KEY and event.value in (0, 1):
                    if event.value:
                        pressed.add(event.code)
                    else:
                        pressed.discard(event.code)
                    changed = False
                    target = mapping.get(event.code)
                    if target is not None:
                        state = int(bool(pressed & sources[target]))
                        if virtual_button_state.get(target, 0) != state:
                            virtual.write(e.EV_KEY, target, state)
                            virtual_button_state[target] = state
                            changed = True
                    for axis, (negative, positive) in ANALOG_BUTTONS.items():
                        if event.code not in (negative, positive):
                            continue
                        state = (-32768 if negative in pressed else 0) + (32767 if positive in pressed else 0)
                        if virtual_axis_state[axis] != state:
                            virtual.write(e.EV_ABS, axis, state)
                            virtual_axis_state[axis] = state
                            changed = True
                    if changed:
                        virtual.syn()
    finally:
        physical.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='DFGT Bridge v0.2')
    parser.add_argument('--learn-shifter', action='store_true', help='Identifica e configura o manche')
    args = parser.parse_args()
    try:
        if args.learn_shifter:
            learn_shifter()
        else:
            main()
    except KeyboardInterrupt:
        print('\nDFGT Bridge encerrado.')
