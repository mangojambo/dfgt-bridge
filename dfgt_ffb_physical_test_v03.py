#!/usr/bin/env python3
"""Teste isolado e reversivel de FF_CONSTANT no Logitech DFGT fisico.

Uso: sudo /usr/bin/python3 dfgt_ffb_physical_test_v03.py --test --strength 20
Fecha o jogo, Oversteer e os bridges antes de executar. NAO implementa FFB do jogo.
"""
import argparse
import time
from evdev import InputDevice, ecodes as e, ff

DFGT = '/dev/input/by-id/usb-046d_Driving_Force_GT-event-joystick'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--test', action='store_true', help='Confirma que deseja gerar dois pulsos fisicos')
    parser.add_argument('--strength', type=int, default=20, help='Porcentagem 5..30; padrao 20')
    parser.add_argument('--duration-ms', type=int, default=600, help='Duração por pulso 200..900 ms; padrao 600')
    args = parser.parse_args()
    if not args.test:
        parser.error('Teste fisico exige --test')
    if not 5 <= args.strength <= 30:
        parser.error('--strength deve estar entre 5 e 30')
    if not 200 <= args.duration_ms <= 900:
        parser.error('--duration-ms deve estar entre 200 e 900')

    dev = InputDevice(DFGT)
    effect_ids = []
    try:
        if (dev.info.vendor, dev.info.product) != (0x046d, 0xc29a):
            raise RuntimeError('Dispositivo nao e DFGT (046d:c29a); abortando')
        if e.FF_CONSTANT not in dev.capabilities().get(e.EV_FF, []):
            raise RuntimeError('DFGT nao anuncia FF_CONSTANT')

        level = round(32767 * args.strength / 100)
        print(f'DFGT identificado: {dev.name} ({dev.path})', flush=True)
        print(f'Forca nominal: {args.strength}% (nivel {level}/32767). '
              f'Dois pulsos opostos de {args.duration_ms} ms.', flush=True)
        print('Segure o aro de modo seguro, sem deixar dedos perto de partes moveis.', flush=True)
        for label, signed_level in [('Pulso 1', level), ('Pulso 2 (sentido oposto)', -level)]:
            effect = ff.Effect(
                e.FF_CONSTANT, -1, 0x4000, ff.Trigger(0, 0),
                ff.Replay(args.duration_ms, 0),
                ff.EffectType(ff_constant_effect=ff.Constant(
                    signed_level, ff.Envelope(0, 0, 0, 0)))
            )
            eid = dev.upload_effect(effect)
            effect_ids.append(eid)
            print(label, flush=True)
            try:
                dev.write(e.EV_FF, eid, 1)
                dev.syn()
                time.sleep(args.duration_ms / 1000 + 0.04)
            finally:
                dev.write(e.EV_FF, eid, 0)
                dev.syn()
            time.sleep(0.6)
        print('Concluido: ambos os efeitos interrompidos.', flush=True)
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
