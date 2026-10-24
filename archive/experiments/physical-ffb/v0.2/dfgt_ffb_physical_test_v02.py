#!/usr/bin/env python3
"""Teste isolado v0.2, breve e limitado de FF_CONSTANT no DFGT físico (SteamOS).

Não cria dispositivos virtuais, não modifica drivers/sysfs e não acessa o G920.
Use com o jogo fechado. O próprio efeito tem duração máxima de 600 ms.
"""
import argparse
import time
from evdev import InputDevice, ecodes as e, ff

DFGT = '/dev/input/by-id/usb-046d_Driving_Force_GT-event-joystick'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--test', action='store_true', help='Autoriza dois pulsos curtos no DFGT físico')
    args = parser.parse_args()
    if not args.test:
        parser.error('Por segurança, execute com --test e o DiRT 5 fechado')
    dev = InputDevice(DFGT)
    effect_ids = []
    try:
        if (dev.info.vendor, dev.info.product) != (0x046d, 0xc29a):
            raise RuntimeError('Dispositivo NÃO é o DFGT (046d:c29a): teste cancelado')
        if e.FF_CONSTANT not in dev.capabilities().get(e.EV_FF, []):
            raise RuntimeError('O dispositivo não anuncia FF_CONSTANT')
        print(f'Dispositivo confirmado: {dev.name} ({dev.path}), PID {dev.info.product:04x}', flush=True)
        print('Dois pulsos alternados, 600 ms cada, força baixa (~12%).', flush=True)
        print('Mantenha as mãos afastadas de pontos de esmagamento e segure o aro com cuidado.', flush=True)
        # A direção é determinada pelo sinal de Constant.level na camada lg4ff.
        for label, level in [('Primeiro sentido', 4000), ('Sentido oposto', -4000)]:
            # O torque do volante é no eixo horizontal (90 graus = 0x4000).
            # Direção 0 significa eixo vertical e pode produzir zero torque.
            effect = ff.Effect(
                e.FF_CONSTANT, -1, 0x4000, ff.Trigger(0, 0), ff.Replay(600, 0),
                ff.EffectType(ff_constant_effect=ff.Constant(level, ff.Envelope(0, 0, 0, 0)))
            )
            eid = dev.upload_effect(effect)
            effect_ids.append(eid)
            print(f'{label}: iniciando pulso...', flush=True)
            dev.write(e.EV_FF, eid, 1)
            dev.syn()
            time.sleep(0.63)
            # Parada explícita, além do timeout do efeito no kernel.
            dev.write(e.EV_FF, eid, 0)
            dev.syn()
            time.sleep(0.65)
        print('Teste concluído; efeitos parados e apagados.', flush=True)
    finally:
        for eid in effect_ids:
            try:
                dev.write(e.EV_FF, eid, 0)
                dev.syn()
            except OSError:
                pass
            try:
                dev.erase_effect(eid)
            except OSError:
                pass
        dev.close()

if __name__ == '__main__':
    main()
