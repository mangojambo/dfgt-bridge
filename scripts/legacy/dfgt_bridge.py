#!/usr/bin/env python3
"""DFGT Bridge v0.3 - DFGT -> gamepad Xbox 360 virtual para SteamOS.

Nao captura exclusivamente o DFGT, nao altera o G920 e nao traduz FFB.
Interrompa com Ctrl+C para remover o dispositivo virtual.
"""
import argparse
import time
from evdev import InputDevice, UInput, AbsInfo, ecodes as e

DFGT = '/dev/input/by-id/usb-046d_Driving_Force_GT-event-joystick'

# Entradas identificadas pelo usuario. A = aumentar marcha; X = reduzir marcha.
# NOTA: os codigos 300 e 301 sao compartilhados pelos comandos fisicos
# LB/RB e pelo manche sequencial; nao ha como diferencia-los pela entrada.
BUTTON_MAP = {
    288: e.BTN_SOUTH,  # X PlayStation -> Xbox A
    290: e.BTN_EAST,   # O PlayStation -> Xbox B
    289: e.BTN_NORTH,  # Quadrado -> Xbox Y (ajuste do usuario)
    291: e.BTN_WEST,   # Triangulo -> Xbox X (ajuste do usuario)
    293: e.BTN_WEST,   # Borboleta esquerda -> Xbox X (reduzir)
    292: e.BTN_SOUTH,  # Borboleta direita -> Xbox A (aumentar)
    301: e.BTN_WEST,   # Manche CIMA -> Xbox X (reduzir)
    300: e.BTN_SOUTH,  # Manche BAIXO -> Xbox A (aumentar)
    299: e.BTN_THUMBL, # L3
    298: e.BTN_THUMBR, # R3
    297: e.BTN_START,
    296: e.BTN_SELECT,
    708: e.BTN_MODE,   # Botao central/Guide
    302: e.BTN_NORTH,  # Clique da rodinha -> Xbox Y
    # 707: buzina, reservado
}

# Emula os sticks por botao. O D-pad fisico continua no ABS_HAT0X/Y.
ANALOG_BUTTONS = {
    e.ABS_Y: (303, 706),   # + / - => left stick cima / baixo
    e.ABS_RX: (295, 294),  # L2 / R2 => right stick esquerda / direita
    e.ABS_RY: (705, 704),  # rodinha cima / baixo => right stick cima / baixo
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


def steering_to_stick(raw, info, physical_degrees, effective_degrees):
    """Full stick travel in +/- (effective_degrees/2) physical degrees."""
    center = (info.min + info.max) / 2.0
    half_span = (info.max - info.min) / 2.0
    effective_half_span = half_span * effective_degrees / physical_degrees
    if effective_half_span <= 0:
        return 0
    value = max(-1.0, min(1.0, (raw - center) / effective_half_span))
    return max(-32768, min(32767, round(value * 32767)))


def pedal_to_trigger(raw, info, deadzone):
    """DFGT released=max -> 0; fully pressed=min -> 255."""
    span = info.max - info.min
    if span <= 0:
        return 0
    fraction = max(0.0, min(1.0, (info.max - raw) / span))
    dz = deadzone / 100.0
    if fraction <= dz:
        return 0
    if fraction >= 1.0 - dz:
        return 255
    return round((fraction - dz) / (1.0 - 2.0 * dz) * 255)


def main(args):
    physical = InputDevice(DFGT)
    try:
        steering = physical.absinfo(e.ABS_X)
        accelerator = physical.absinfo(e.ABS_Y)
        brake = physical.absinfo(e.ABS_Z)
        print('DFGT físico:', physical.name, flush=True)
        print(f'Direção: {steering.min}..{steering.max}; faixa virtual: {args.steering_degrees:g}°', flush=True)
        print(f'Pedais: acelerador {accelerator.min}..{accelerator.max}; freio {brake.min}..{brake.max}', flush=True)
        print('Manche cima/borboleta esquerda => X; baixo/direita => A', flush=True)
        print('FFB: nao traduzido; DFGT físico continua visivel para o jogo.', flush=True)

        with UInput(
            {e.EV_KEY: BUTTONS, e.EV_ABS: AXES},
            name='Microsoft X-Box 360 pad',
            vendor=0x045e, product=0x028e, version=0x0110,
            bustype=e.BUS_USB,
        ) as virtual:
            print('Xbox virtual:', virtual.device.path, flush=True)
            print('Ctrl+C para encerrar.', flush=True)
            raw_x, raw_accel, raw_brake = steering.value, accelerator.value, brake.value
            x_value = steering_to_stick(raw_x, steering, args.physical_degrees, args.steering_degrees)
            rt = pedal_to_trigger(raw_accel, accelerator, args.pedal_deadzone)
            lt = pedal_to_trigger(raw_brake, brake, args.pedal_deadzone)
            initial = {
                e.ABS_X: x_value,
                e.ABS_Y: 0, e.ABS_RX: 0, e.ABS_RY: 0,
                e.ABS_Z: lt, e.ABS_RZ: rt,
                e.ABS_HAT0X: 0, e.ABS_HAT0Y: 0,
            }
            for code, value in initial.items():
                virtual.write(e.EV_ABS, code, value)
            virtual.syn()

            button_sources = {}
            for source, target in BUTTON_MAP.items():
                button_sources.setdefault(target, set()).add(source)
            pressed = set()
            buttons_sent = {target: 0 for target in button_sources}
            analog_sent = {axis: 0 for axis in ANALOG_BUTTONS}
            last_status = 0.0
            for event in physical.read_loop():
                if event.type == e.EV_ABS:
                    if event.code == e.ABS_X:
                        raw_x = event.value
                        dest = e.ABS_X
                        val = steering_to_stick(raw_x, steering, args.physical_degrees, args.steering_degrees)
                        x_value = val
                    elif event.code == e.ABS_Y:
                        raw_accel = event.value
                        dest = e.ABS_RZ  # RT = acelerador
                        val = pedal_to_trigger(raw_accel, accelerator, args.pedal_deadzone)
                        rt = val
                    elif event.code == e.ABS_Z:
                        raw_brake = event.value
                        dest = e.ABS_Z   # LT = freio
                        val = pedal_to_trigger(raw_brake, brake, args.pedal_deadzone)
                        lt = val
                    elif event.code in (e.ABS_HAT0X, e.ABS_HAT0Y):
                        dest, val = event.code, event.value
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
                    target = BUTTON_MAP.get(event.code)
                    if target is not None:
                        state = int(bool(pressed & button_sources[target]))
                        if buttons_sent[target] != state:
                            virtual.write(e.EV_KEY, target, state)
                            buttons_sent[target] = state
                            changed = True
                    for axis, (negative, positive) in ANALOG_BUTTONS.items():
                        if event.code not in (negative, positive):
                            continue
                        state = (-32768 if negative in pressed else 0) + (32767 if positive in pressed else 0)
                        if analog_sent[axis] != state:
                            virtual.write(e.EV_ABS, axis, state)
                            analog_sent[axis] = state
                            changed = True
                    if changed:
                        virtual.syn()
                else:
                    continue

                if args.monitor and time.monotonic() - last_status >= 0.25:
                    print(f'DFGT: X={raw_x:5d} Acel={raw_accel:3d} Freio={raw_brake:3d} '
                          f'| Xbox: LX={x_value:6d} RT={rt:3d} LT={lt:3d}', flush=True)
                    last_status = time.monotonic()
    finally:
        physical.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='DFGT Bridge v0.3 para SteamOS')
    parser.add_argument('--steering-degrees', type=float, default=180,
                        help='Curso efetivo de direcao em graus, ex.: 180, 270, 360 (padrao: 180)')
    parser.add_argument('--physical-degrees', type=float, default=900,
                        help='Curso fisico configurado no DFGT (padrao: 900)')
    parser.add_argument('--pedal-deadzone', type=float, default=2.0,
                        help='Zona morta em porcentagem em cada ponta do pedal (padrao: 2)')
    parser.add_argument('--monitor', action='store_true',
                        help='Mostra valores brutos e virtuais para diagnostico')
    args = parser.parse_args()
    if not 0 < args.steering_degrees <= args.physical_degrees:
        parser.error('steering-degrees deve estar entre 0 e physical-degrees')
    if not 0 <= args.pedal_deadzone < 40:
        parser.error('pedal-deadzone deve estar entre 0 e 40')
    try:
        main(args)
    except KeyboardInterrupt:
        print('\nDFGT Bridge encerrado.')
