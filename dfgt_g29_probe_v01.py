#!/usr/bin/env python3
"""DFGT -> G29-like evdev device; recognition probe, NOT full G29 USB HID emulation.

Does not grab/hide the physical wheel, control its rotation or implement FFB.
Designed only to validate enumeration and input on SteamOS/Proton.
"""
import argparse
import time
from evdev import InputDevice, UInput, AbsInfo, ecodes as e

DFGT = '/dev/input/by-id/usb-046d_Driving_Force_GT-event-joystick'
VIRTUAL_NAME = 'Logitech G29 Driving Force Racing Wheel'
AXES = {
    e.ABS_X: AbsInfo(32768, 0, 65535, 0, 0, 0),
    e.ABS_Y: AbsInfo(255, 0, 255, 0, 0, 0),
    e.ABS_Z: AbsInfo(255, 0, 255, 0, 0, 0),
    e.ABS_RZ: AbsInfo(255, 0, 255, 0, 0, 0),  # clutch not present on DFGT
    e.ABS_HAT0X: AbsInfo(0, -1, 1, 0, 0, 0),
    e.ABS_HAT0Y: AbsInfo(0, -1, 1, 0, 0, 0),
}

def map_steering(raw, low, high):
    if high <= low:
        raise ValueError('Invalid physical steering axis range')
    norm = max(0., min(1., (raw - low) / (high - low)))
    return round(norm * 65535)

def run(monitor=False):
    physical = InputDevice(DFGT)
    try:
        xinfo = physical.absinfo(e.ABS_X)
        ainfo = physical.absinfo(e.ABS_Y)
        binfo = physical.absinfo(e.ABS_Z)
        keys = sorted(set(physical.capabilities().get(e.EV_KEY, [])))
        if not keys:
            raise RuntimeError('Physical wheel has no button capabilities')
        with UInput(
            {e.EV_KEY: keys, e.EV_ABS: AXES},
            name=VIRTUAL_NAME,
            vendor=0x046d, product=0xc24f, version=0x0110,
            bustype=e.BUS_USB,
        ) as virtual:
            print('Físico:', physical.name, '(046d:c29a)', flush=True)
            print('Virtual:', VIRTUAL_NAME, '(046d:c24f)', virtual.device.path, flush=True)
            print('AVISO: identidade evdev apenas, sem HIDRAW G29 ou FFB virtual.', flush=True)
            print('Não oculta o DFGT físico. Ctrl+C para encerrar.', flush=True)

            x = xinfo.value
            a = ainfo.value
            b = binfo.value
            initial = {
                e.ABS_X: map_steering(x, xinfo.min, xinfo.max),
                e.ABS_Y: a,
                e.ABS_Z: b,
                e.ABS_RZ: 255,
                e.ABS_HAT0X: 0,
                e.ABS_HAT0Y: 0,
            }
            for code, value in initial.items():
                virtual.write(e.EV_ABS, code, value)
            virtual.syn()

            last_log = 0.0
            for event in physical.read_loop():
                if event.type == e.EV_ABS:
                    if event.code == e.ABS_X:
                        x = event.value
                        virtual.write(e.EV_ABS, e.ABS_X,
                                      map_steering(x, xinfo.min, xinfo.max))
                    elif event.code == e.ABS_Y:
                        a = event.value
                        virtual.write(e.EV_ABS, e.ABS_Y, a)
                    elif event.code == e.ABS_Z:
                        b = event.value
                        virtual.write(e.EV_ABS, e.ABS_Z, b)
                    elif event.code in (e.ABS_HAT0X, e.ABS_HAT0Y):
                        virtual.write(e.EV_ABS, event.code, event.value)
                    else:
                        continue
                elif event.type == e.EV_KEY and event.code in keys and event.value in (0, 1):
                    virtual.write(e.EV_KEY, event.code, event.value)
                else:
                    continue
                virtual.syn()
                if monitor and time.monotonic() - last_log > 0.25:
                    print(f'DFGT X={x:5d} -> G29 X={map_steering(x, xinfo.min, xinfo.max):5d} '
                          f'| acelerador={a:3d} freio={b:3d}', flush=True)
                    last_log = time.monotonic()
    finally:
        physical.close()

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='G29-like uinput recognition probe')
    parser.add_argument('--monitor', action='store_true', help='Display real-time input values')
    args = parser.parse_args()
    try:
        run(args.monitor)
    except KeyboardInterrupt:
        print('\nVolante virtual removido.')
