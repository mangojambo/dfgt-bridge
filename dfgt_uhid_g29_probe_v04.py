#!/usr/bin/env python3
"""DFGT -> UHID G29 input + complete passive FFB capture v0.4 for SteamOS.

Experimental proof of concept. Creates a Linux HID device, not a full G29.
Passive FFB observation ONLY; no motor commands, no kernel changes and no persistent installation.
Run with sudo because /dev/uhid is root-only; exit with Ctrl+C.

UHID creation approach inspired by SameBrain-Chris/uhid-sim-relay (MIT):
https://github.com/SameBrain-Chris/uhid-sim-relay
"""
import argparse
from collections import Counter
import glob
import os
import select
import struct
import time
from evdev import InputDevice, ecodes as e

SOURCE = '/dev/input/by-id/usb-046d_Driving_Force_GT-event-joystick'
UHID_CREATE2 = 11
UHID_DESTROY = 1
UHID_INPUT2 = 12
UHID_GET_REPORT = 9
UHID_GET_REPORT_REPLY = 10
UHID_SET_REPORT = 13
UHID_SET_REPORT_REPLY = 14
UHID_OUTPUT = 6
UHID_DATA_MAX = 4096

# HID Button 1..16 corresponds to the wheel's event keys 288..303;
# 17..25 are the additional DFGT keys 704..712.
BUTTON_CODES = list(range(288, 304)) + list(range(704, 713))
BUTTON_BITS = {code: bit for bit, code in enumerate(BUTTON_CODES)}


def descriptor():
    """Generic Desktop Joystick: X16, Z8, Rz8, Y8, hat 4, 25 buttons."""
    return bytes([
        0x05, 0x01,             # Usage Page: Generic Desktop
        0x09, 0x04,             # Usage: Joystick
        0xA1, 0x01,             # Collection: Application
        0x09, 0x01,             # Usage: Pointer
        0xA1, 0x00,             # Collection: Physical
        0x09, 0x30,             # Usage: X (steering)
        0x15, 0x00,             # Logical min 0
        0x27, 0xFF, 0xFF, 0x00, 0x00, # Logical max 65535 (32-bit)
        0x75, 0x10,             # 16 bits
        0x95, 0x01,             # 1 axis
        0x81, 0x02,             # Input Data,Var,Abs
        0x09, 0x32,             # Z: accelerator
        0x09, 0x35,             # Rz: brake
        0x09, 0x31,             # Y: clutch
        0x15, 0x00,
        0x26, 0xFF, 0x00,       # Logical max 255
        0x75, 0x08,
        0x95, 0x03,
        0x81, 0x02,
        0xC0,                   # End physical collection
        0x09, 0x39,             # Hat switch
        0x15, 0x00,
        0x25, 0x07,
        0x35, 0x00,
        0x46, 0x3B, 0x01,       # Physical max 315 degrees
        0x65, 0x14,             # Unit: english rotation degrees
        0x75, 0x04,
        0x95, 0x01,
        0x81, 0x42,             # Data,Var,Abs,Null State (8=neutral)
        0x65, 0x00,             # Reset unit
        0x75, 0x04,
        0x95, 0x01,
        0x81, 0x03,             # Constant padding
        0x05, 0x09,             # Usage Page Buttons
        0x19, 0x01,
        0x29, 0x19,             # 25 buttons
        0x15, 0x00,
        0x25, 0x01,
        0x75, 0x01,
        0x95, 0x19,
        0x81, 0x02,
        0x75, 0x01,
        0x95, 0x07,             # 7 unused bits
        0x81, 0x03,
        0xC0,                   # End application collection
    ])


def create_device(fd, name, vid, pid):
    rd = descriptor()
    name_bytes = name.encode('utf-8')[:127].ljust(128, b'\0')
    body = name_bytes + bytes(64) + bytes(64)
    # bus=BUS_USB (0x03) is a virtual HID transport identity, not a real USB device.
    body += struct.pack('<HHIIII', len(rd), 0x03, vid, pid, 0x0110, 0)
    os.write(fd, struct.pack('<I', UHID_CREATE2) + body + rd)


def report(state, gas_axis='z', invert_gas=False):
    steering, accel, brake, hatx, haty, pressed = state
    gas = 255 - accel if invert_gas else accel
    # G29-like report layout remains X, Z, Rz, Y. Test alternative
    # throttle axes while leaving the working brake Rz unchanged.
    z_gas = gas if gas_axis in ('z', 'both') else 255
    y_gas = gas if gas_axis in ('y', 'both') else 255
    x = min(65535, max(0, round(steering * 65535 / 16383)))
    hat = {
        (0, -1): 0, (1, -1): 1, (1, 0): 2, (1, 1): 3,
        (0, 1): 4, (-1, 1): 5, (-1, 0): 6, (-1, -1): 7,
    }.get((hatx, haty), 8)
    buttons = 0
    for code in pressed:
        if code in BUTTON_BITS:
            buttons |= 1 << BUTTON_BITS[code]
    return struct.pack('<HBBBBI', x, z_gas, brake, y_gas, hat, buttons)


def send_input(fd, data):
    if len(data) > UHID_DATA_MAX:
        raise ValueError('HID report too large')
    os.write(fd, struct.pack('<IH', UHID_INPUT2, len(data)) + data +
             bytes(UHID_DATA_MAX - len(data)))


class FFBMonitor:
    """Passive UHID traffic inspection: never sends FFB to the physical wheel."""
    def __init__(self, enabled=False, capture_path=None):
        self.enabled = enabled
        self.counts = {'OUTPUT': 0, 'SET_REPORT': 0, 'GET_REPORT': 0}
        self.last = {}
        self.capture_path = capture_path
        self.capture_file = None
        self.opcodes = Counter()
        self.distinct_reports = set()
        if capture_path:
            # Capture opens exclusively; never silently overwrite an earlier run.
            self.capture_file = open(capture_path, 'x', encoding='utf-8', buffering=131072)
            self.capture_file.write('timestamp_ns,kind,report_type,report_num,hex_bytes\n')

    def capture(self, kind, payload, report_type=-1, report_num=-1):
        if self.capture_file is None:
            return
        hexdata = payload.hex()
        self.capture_file.write(f'{time.time_ns()},{kind},{report_type},{report_num},{hexdata}\n')
        if kind == 'OUTPUT' and payload:
            self.opcodes[payload[:2].hex()] += 1
            if len(self.distinct_reports) < 100000:
                self.distinct_reports.add(hexdata)

    def close(self):
        if self.capture_file is not None:
            self.capture_file.close()
            self.capture_file = None

    def log(self, kind, details):
        if not self.enabled:
            return
        if kind in self.counts:
            self.counts[kind] += 1
            n = self.counts[kind]
            # Keep output bounded for busy games, and retain a fresh sample.
            if n > 60 and n % 100 != 0:
                return
            print(f'[UHID FFB] {kind} #{n}: {details}', flush=True)
        else:
            print(f'[UHID FFB] {kind}: {details}', flush=True)

    def summary(self):
        if self.enabled:
            print('[UHID FFB] RESUMO: ' + ', '.join(
                f'{kind}={count}' for kind, count in self.counts.items()), flush=True)
        if self.capture_path:
            print('[UHID FFB] CAPTURA: ' + self.capture_path, flush=True)
            print('[UHID FFB] Prefixos OUTPUT mais comuns: ' +
                  repr(self.opcodes.most_common(12)), flush=True)
            print('[UHID FFB] Relatórios OUTPUT distintos: ' +
                  str(len(self.distinct_reports)), flush=True)


def respond_to_kernel(fd, event, ffb_monitor=None):
    if len(event) < 4:
        return
    event_type = struct.unpack_from('<I', event)[0]
    if ffb_monitor and (ffb_monitor.enabled or ffb_monitor.capture_file is not None):
        if event_type == UHID_OUTPUT:
            if len(event) >= 4 + UHID_DATA_MAX + 3:
                size = struct.unpack_from('<H', event, 4 + UHID_DATA_MAX)[0]
                rtype = event[4 + UHID_DATA_MAX + 2]
                payload = event[4:4 + min(size, UHID_DATA_MAX)]
                ffb_monitor.capture('OUTPUT', payload, report_type=rtype)
                ffb_monitor.log('OUTPUT', f'type={rtype} len={size} bytes={payload[:32].hex(" ")}')
        elif event_type == UHID_SET_REPORT and len(event) >= 12:
            report_num, report_type = event[8], event[9]
            size = struct.unpack_from('<H', event, 10)[0]
            payload = event[12:12 + min(size, UHID_DATA_MAX)]
            ffb_monitor.capture('SET_REPORT', payload, report_type=report_type, report_num=report_num)
            ffb_monitor.log('SET_REPORT', f'num={report_num} type={report_type} len={size} bytes={payload[:32].hex(" ")}')
        elif event_type == UHID_GET_REPORT and len(event) >= 10:
            ffb_monitor.capture('GET_REPORT', b'', report_type=event[9], report_num=event[8])
            ffb_monitor.log('GET_REPORT', f'num={event[8]} type={event[9]}')
        elif event_type == 4:
            ffb_monitor.log('OPEN', 'Interface HID aberta')
        elif event_type == 5:
            ffb_monitor.log('CLOSE', 'Interface HID fechada')
    if event_type == UHID_GET_REPORT and len(event) >= 8:
        request_id = struct.unpack_from('<I', event, 4)[0]
        # This input-only test has no feature reports.
        os.write(fd, struct.pack('<IIHH', UHID_GET_REPORT_REPLY,
                                 request_id, 95, 0))  # EOPNOTSUPP
    elif event_type == UHID_SET_REPORT and len(event) >= 8:
        request_id = struct.unpack_from('<I', event, 4)[0]
        os.write(fd, struct.pack('<IIH', UHID_SET_REPORT_REPLY,
                                 request_id, 95))
    # UHID_OUTPUT intentionally ignored: no force feedback forwarded.



def hid_g29_nodes():
    return set(glob.glob('/sys/bus/hid/devices/0003:046D:C24F.*'))


def bind_virtual_g29(before):
    """Temporarily permit hid-generic, binding only our newly created UHID."""
    param = '/sys/module/hid/parameters/ignore_special_drivers'
    bind = '/sys/bus/hid/drivers/hid-generic/bind'
    target = None
    for _ in range(150):
        created = hid_g29_nodes() - before
        virtual = [p for p in created if '/virtual/misc/uhid/' in os.path.realpath(p)]
        if len(virtual) == 1:
            target = virtual[0]
            break
        if len(virtual) > 1:
            raise RuntimeError('Múltiplos G29 virtuais novos: encerrando por segurança')
        time.sleep(0.02)
    if not target:
        raise RuntimeError('G29 UHID não apareceu no sysfs após criação')
    if os.path.islink(os.path.join(target, 'driver')):
        print('G29 já tem driver:', os.path.realpath(os.path.join(target, 'driver')))
        return
    with open(param, 'rb') as f:
        original = f.read().strip()
    try:
        with open(param, 'wb') as f:
            f.write(b'1\n')
        with open(bind, 'wb') as f:
            f.write(os.path.basename(target).encode('ascii') + b'\n')
    finally:
        with open(param, 'wb') as f:
            f.write(original + b'\n')
    link = os.path.join(target, 'driver')
    if not os.path.islink(link) or not os.path.realpath(link).endswith('/hid-generic'):
        raise RuntimeError('Falha ao associar hid-generic ao G29 UHID')
    print('G29 virtual associado a hid-generic; parâmetro global restaurado.', flush=True)


def run(args):
    source = InputDevice(SOURCE)
    fd = -1
    grabbed = False
    ffb_monitor = FFBMonitor(args.ffb_monitor, args.ffb_capture)
    try:
        info_x = source.absinfo(e.ABS_X)
        steer = info_x.value
        accel = source.absinfo(e.ABS_Y).value
        brake = source.absinfo(e.ABS_Z).value
        hatx, haty = 0, 0
        pressed = set()
        vid, pid = (0x046d, 0xc24f) if args.identity == 'g29' else (0x1209, 0xd029)
        name = ('Logitech G29 Driving Force Racing Wheel' if args.identity == 'g29'
                else 'DFGT UHID Steering Wheel Probe')
        g29_before = hid_g29_nodes() if args.auto_bind and args.identity == 'g29' else None
        fd = os.open('/dev/uhid', os.O_RDWR | os.O_CLOEXEC | os.O_NONBLOCK)
        create_device(fd, name, vid, pid)
        if g29_before is not None:
            bind_virtual_g29(g29_before)
        if args.grab:
            source.grab()
            grabbed = True
        print(f'DFGT físico: {source.name} (046d:c29a)')
        print(f'UHID virtual: {name} ({vid:04x}:{pid:04x})')
        print(f'HID: direção X16, acelerador {args.gas_axis.upper()}8, freio Rz8, hat, 25 botões')
        print(f'Acelerador invertido: {"SIM" if args.invert_gas else "NÃO"}')
        print('FFB PASSIVO: não envia nenhuma força. G920 permanece intocado.')
        if args.ffb_monitor:
            print('[UHID FFB] Monitor de OUTPUT/SET_REPORT/GET_REPORT ativado.', flush=True)
        if args.ffb_capture:
            print('[UHID FFB] Gravando todos os pacotes em: ' + args.ffb_capture, flush=True)
        print(f'Grab do evdev físico: {"SIM" if grabbed else "NÃO"}')
        print('Ctrl+C remove o dispositivo virtual.', flush=True)
        state = (steer, accel, brake, hatx, haty, pressed)
        send_input(fd, report(state, args.gas_axis, args.invert_gas))
        poller = select.poll()
        poller.register(source.fd, select.POLLIN)
        poller.register(fd, select.POLLIN)
        last_log = 0.0
        dirty = False
        while True:
            for ready_fd, _ in poller.poll(250):
                if ready_fd == fd:
                    while True:
                        try:
                            data = os.read(fd, 4380)
                        except BlockingIOError:
                            break
                        if not data:
                            break
                        respond_to_kernel(fd, data, ffb_monitor)
                elif ready_fd == source.fd:
                    for ev in source.read():
                        if ev.type == e.EV_ABS:
                            if ev.code == e.ABS_X:
                                steer = ev.value
                            elif ev.code == e.ABS_Y:
                                accel = ev.value
                            elif ev.code == e.ABS_Z:
                                brake = ev.value
                            elif ev.code == e.ABS_HAT0X:
                                hatx = ev.value
                            elif ev.code == e.ABS_HAT0Y:
                                haty = ev.value
                            else:
                                continue
                            dirty = True
                        elif ev.type == e.EV_KEY and ev.value in (0, 1):
                            if ev.value:
                                pressed.add(ev.code)
                            else:
                                pressed.discard(ev.code)
                            dirty = True
                        elif ev.type == e.EV_SYN and ev.code == e.SYN_REPORT and dirty:
                            state = (steer, accel, brake, hatx, haty, pressed)
                            send_input(fd, report(state, args.gas_axis, args.invert_gas))
                            dirty = False
                            if args.monitor and time.monotonic() - last_log >= 0.25:
                                hid_x = round(steer * 65535 / 16383)
                                print(f'DFGT X={steer:5d} => HID X={hid_x:5d} | '
                                      f'gas={255 - accel if args.invert_gas else accel:3d} '
                                      f'eixo={args.gas_axis.upper()} Rz(freio)={brake:3d} | '
                                      f'botões={len(pressed)}', flush=True)
                                last_log = time.monotonic()
    finally:
        ffb_monitor.summary()
        ffb_monitor.close()
        if grabbed:
            try:
                source.ungrab()
            except OSError:
                pass
        if fd >= 0:
            try:
                os.write(fd, struct.pack('<I', UHID_DESTROY))
            except OSError:
                pass
            os.close(fd)
        source.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='DFGT UHID G29 HID probe v0.4 (captura passiva completa de FFB)')
    parser.add_argument('--identity', choices=['generic', 'g29'], default='g29',
                        help='Identidade virtual; g29 é experimental (padrão)')
    parser.add_argument('--grab', action='store_true',
                        help='Oculta eventos evdev físicos de outros leitores durante o teste')
    parser.add_argument('--monitor', action='store_true')
    parser.add_argument('--ffb-monitor', action='store_true',
                        help='Mostra relatórios HID de saída enviados pelo jogo; NÃO aciona motor')
    parser.add_argument('--ffb-capture', metavar='ARQUIVO_CSV',
                        help='Salva TODOS os relatórios FFB HID recebidos; nunca aciona o motor')
    parser.add_argument('--auto-bind', action='store_true',
                        help='Associe o G29 virtual ao hid-generic e restaure o parâmetro global')
    parser.add_argument('--gas-axis', choices=['z', 'y', 'both'], default='z',
                        help='Onde colocar o acelerador no HID (padrão z)')
    parser.add_argument('--invert-gas', action='store_true',
                        help='Inverte somente o acelerador (teste)')
    args = parser.parse_args()
    try:
        run(args)
    except KeyboardInterrupt:
        print('\nUHID virtual encerrado.')
    except PermissionError as exc:
        parser.error(f'{exc}. /dev/uhid é root-only: teste com sudo.')
