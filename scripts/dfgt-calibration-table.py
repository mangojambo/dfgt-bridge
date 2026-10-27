#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Print a synthetic Spring comparison through the real decoder and mock output."""
import argparse
from pathlib import Path
import struct
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from dfgt_bridge.backends.mock import MockBackend
from dfgt_bridge.ffb.effects import PIDSteeringBridge
from dfgt_bridge.ffb.mixer import validate_effect_gains
from dfgt_bridge.ffb.renderer import MotionState
from dfgt_bridge.supervised import SupervisedPolicy


def comparison(spring_gain=2, coefficient=32767, saturation=65535, deadband=0):
    validate_effect_gains({'spring': spring_gain})
    if not (type(coefficient) is int and 0 <= coefficient <= 32767 and
            type(saturation) is int and 0 <= saturation <= 65535 and
            type(deadband) is int and 0 <= deadband <= 65535):
        raise ValueError('Coeficiente 0..32767; saturação/deadband 0..65535')
    now = 0
    engine = PIDSteeringBridge(clock=lambda: now/1e9)
    engine.arm()
    engine.set_report(bytes.fromhex('54090000'), 0x54)
    block = engine.last_block
    engine.write_output(bytes((0x51, block, 9)) + struct.pack('<HHHHBBBHH',
        0xffff, 0, 0, 0, 100, 255, 1, 9000, 0))
    engine.write_output(bytes((0x53, block, 0)) + struct.pack('<hhhHHH',
        0, coefficient, coefficient, saturation, saturation, deadband))
    engine.write_output(bytes((0x5a, block, 1, 1)))
    policies = [SupervisedPolicy(MockBackend(clock=lambda: now/1e9), effect_gains={'spring': gain})
                for gain in (1, spring_gain)]
    rows = []
    try:
        for position in (-1, -.5, -.25, -.1, 0, .1, .25, .5, 1):
            results = []
            for _ in range(10):
                now += 25_000_000
                components = engine.components(motion=MotionState(position))
                results = [p.apply('frame', dict(generation=p.last_generation+1,
                    calculated_ns=now, components=components), now) for p in policies]
            before, after = results
            rows.append(dict(position=position, spring=components['spring'],
                before=before['evdev_level'], calibrated_spring=after['effect_calibration']['components']['spring'],
                after=after['evdev_level'], utilization=abs(after['evdev_level'])/32767*100,
                clipped=after['mix']['clipped']))
    finally:
        for policy in policies:
            policy.apply('close', {}, now)
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--spring-gain', type=float, default=2)
    parser.add_argument('--coefficient', type=int, default=32767)
    parser.add_argument('--saturation', type=int, default=65535)
    parser.add_argument('--deadband', type=int, default=0)
    args = parser.parse_args()
    try:
        rows = comparison(args.spring_gain, args.coefficient, args.saturation, args.deadband)
    except ValueError as exc:
        parser.error(str(exc))
    print('Synthetic vectors; MockBackend only; no measured torque. Global gain 100%, polarity -1, slew 327670.')
    print(f'Spring gain 1 -> {args.spring_gain:g}; coefficient {args.coefficient}; saturation {args.saturation}; deadband {args.deadband}.\n')
    print('| Position | PID Spring | EVDEV before | Calibrated Spring | EVDEV after | Command range after | Final clipping |')
    print('|---:|---:|---:|---:|---:|---:|:---:|')
    for row in rows:
        print(f'| {row["position"]:+.0%} | {row["spring"]:.2f} | {row["before"]} | '
              f'{row["calibrated_spring"]:.2f} | {row["after"]} | {row["utilization"]:.1f}% | '
              f'{"yes" if row["clipped"] else "no"} |')


if __name__ == '__main__':
    main()
