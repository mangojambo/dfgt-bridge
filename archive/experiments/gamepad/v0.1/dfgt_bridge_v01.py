#!/usr/bin/env python3
import time
from evdev import InputDevice, UInput, ecodes as e, AbsInfo

DFGT = "/dev/input/by-id/usb-046d_Driving_Force_GT-event-joystick"

axes = {
    e.ABS_X: AbsInfo(0, -32768, 32767, 0, 0, 0),
    e.ABS_Y: AbsInfo(0, -32768, 32767, 0, 0, 0),
    e.ABS_RX: AbsInfo(0, -32768, 32767, 0, 0, 0),
    e.ABS_RY: AbsInfo(0, -32768, 32767, 0, 0, 0),
    e.ABS_Z: AbsInfo(0, 0, 255, 0, 0, 0),
    e.ABS_RZ: AbsInfo(0, 0, 255, 0, 0, 0),
    e.ABS_HAT0X: AbsInfo(0, -1, 1, 0, 0, 0),
    e.ABS_HAT0Y: AbsInfo(0, -1, 1, 0, 0, 0),
}

buttons = [
    e.BTN_SOUTH, e.BTN_EAST, e.BTN_WEST, e.BTN_NORTH,
    e.BTN_TL, e.BTN_TR, e.BTN_SELECT, e.BTN_START,
    e.BTN_MODE, e.BTN_THUMBL, e.BTN_THUMBR,
]

# Mapeamento inicial, ajustável posteriormente
keymap = {
    288: e.BTN_SOUTH,
    289: e.BTN_EAST,
    290: e.BTN_WEST,
    291: e.BTN_NORTH,
    294: e.BTN_TL,
    295: e.BTN_TR,
    296: e.BTN_SELECT,
    298: e.BTN_START,
    299: e.BTN_MODE,
    300: e.BTN_THUMBL,
    301: e.BTN_THUMBR,
}

def normalize(value, info, out_min, out_max, invert=False):
    span = info.max - info.min
    if span <= 0:
        return out_min
    ratio = max(0.0, min(1.0, (value - info.min) / span))
    if invert:
        ratio = 1.0 - ratio
    return round(out_min + ratio * (out_max - out_min))

def main():
    physical = InputDevice(DFGT)
    try:
        steering = physical.absinfo(e.ABS_X)
        accelerator = physical.absinfo(e.ABS_Y)
        brake = physical.absinfo(e.ABS_Z)

        print("DFGT físico:", physical.name)
        print("Direção:", steering.min, steering.max)
        print("Acelerador:", accelerator.min, accelerator.max)
        print("Freio:", brake.min, brake.max)

        with UInput(
            {e.EV_KEY: buttons, e.EV_ABS: axes},
            name="Microsoft X-Box 360 pad",
            vendor=0x045e,
            product=0x028e,
            version=0x0110,
            bustype=e.BUS_USB,
        ) as virtual:
            print("Xbox virtual criado:", virtual.device.path)
            print("Pressione Ctrl+C para encerrar.")

            # Valores iniciais neutros
            virtual.write(e.EV_ABS, e.ABS_X,
                          normalize(steering.value, steering, -32768, 32767))
            virtual.write(e.EV_ABS, e.ABS_RZ,
                          normalize(accelerator.value, accelerator, 0, 255, True))
            virtual.write(e.EV_ABS, e.ABS_Z,
                          normalize(brake.value, brake, 0, 255, True))
            virtual.syn()

            for event in physical.read_loop():
                if event.type == e.EV_ABS:
                    if event.code == e.ABS_X:
                        virtual.write(e.EV_ABS, e.ABS_X,
                            normalize(event.value, steering, -32768, 32767))
                    elif event.code == e.ABS_Y:
                        virtual.write(e.EV_ABS, e.ABS_RZ,
                            normalize(event.value, accelerator, 0, 255, True))
                    elif event.code == e.ABS_Z:
                        virtual.write(e.EV_ABS, e.ABS_Z,
                            normalize(event.value, brake, 0, 255, True))
                    elif event.code in (e.ABS_HAT0X, e.ABS_HAT0Y):
                        virtual.write(e.EV_ABS, event.code, event.value)
                    else:
                        continue

                elif event.type == e.EV_KEY:
                    mapped = keymap.get(event.code)
                    if mapped is None:
                        continue
                    virtual.write(e.EV_KEY, mapped, int(event.value != 0))
                else:
                    continue

                virtual.syn()
    finally:
        physical.close()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nDFGT Bridge encerrado.")
