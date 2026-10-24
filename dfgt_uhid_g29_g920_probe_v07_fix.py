#!/usr/bin/env python3
"""DFGT -> UHID G29/G920 with passive HID PID FFB capture v0.7-fix for SteamOS.

Copyright (c) 2026. SPDX-License-Identifier: GPL-2.0-only.
HID PID descriptor adapted from mescon/logitech-trueforce-linux-driver,
userspace/logi-wheel/crates/logi-ffb/src/descriptor.rs (GPL-2.0-only):
https://github.com/mescon/logitech-trueforce-linux-driver


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
import threading
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


# The upstream PID report collection uses report ids 0x50..0x5D. We keep
# our working joystick axis/button layout and add report ID 0x01 to it.
# IMPORTANT: once *any* report id is present in a descriptor, every input
# report has to include the id byte (including joystick events).
PID_DESCRIPTOR = bytes.fromhex("""
35004500050F0992A1028550099F09A0099415002501750195088102092215012528750795018102C00921A10285510922150125287508950191020925A10209260927092809300931093209330934094009410942094315012512750895019100C009500954095109A7150026FF7F66031055FD7510950491025500660000095215002664007508950191020953150026FF007508950191020955A1020B300001000B3100010015002501750195029102C009567501950191027505950191030957A1020B01000A000B02000A0066140055FE1500273C8C00007510950291025500660000C0C0050F095AA1028552092215012528750895019102095B095D150026FF7F461027751095029102095C095E66031055FD26FF7F75109502910245006600005500C0095FA102855309221501252875089501910209231500250175089501910209600961096216008026FF7F36F0D8461027751095039102096309640965150027FFFF000035004610277510950391024500C0096EA102855D0922150125287508950191020970150026FF7F3500461027751095019102096F16008026FF7F36F0D84610277510950191023500450009711500273C8C000066140055FE7510950191020972150026FF7F66031055FD75109501910265005500C00973A1028555092215012528750895019102097016008026FF7F36F0D846102775109501910235004500C0050F0977A102855A09221501252835014528750895019102350045000978A1020979097A097B15012503750895019100C0097C150026FF0046FF0091024500C00996A1028550099709980999099A099B099C15012506750895019100C009ABA10285540925A1020926092709280930093109320933093409400941094209431501251275089501B100C00501093B150026FF0146FF01750A9501B1027506B1014500C0050F0989A10285560922252815013501452875089501B102098BA102098C098D098E250315013501450375089501B100C009AC150027FFFF0000350047FFFF000075109501B1004500C0097FA1028557098075109501150027FFFF0000B102098326FF0075089501B10209A909AA7501950215002501B10275069501B103C0097DA1028559097E26FF00750895019102C00974A102855809221501252875089501910209750976150026FF00750895029102C0
""")
PID_BLOCK_FREE = bytes.fromhex('050F0990A102855B092215012528750895019102C0')


def descriptor():
    """v0.4 joystick layout + numbered input report + PID FFB outputs/features."""
    # Preserve all the v0.4 axis usages and bit layout. Report ID 1 added.
    original = bytes([
        0x05, 0x01, 0x09, 0x04, 0xA1, 0x01,  # Generic Desktop / Joystick App
        0x09, 0x01, 0xA1, 0x00, 0x09, 0x30,
        0x15, 0x00, 0x27, 0xFF, 0xFF, 0x00, 0x00,
        0x75, 0x10, 0x95, 0x01, 0x81, 0x02,
        0x09, 0x32, 0x09, 0x35, 0x09, 0x31,
        0x15, 0x00, 0x26, 0xFF, 0x00,
        0x75, 0x08, 0x95, 0x03, 0x81, 0x02,
        0xC0,
        0x09, 0x39, 0x15, 0x00, 0x25, 0x07,
        0x35, 0x00, 0x46, 0x3B, 0x01, 0x65, 0x14,
        0x75, 0x04, 0x95, 0x01, 0x81, 0x42,
        0x65, 0x00, 0x75, 0x04, 0x95, 0x01, 0x81, 0x03,
        0x05, 0x09, 0x19, 0x01, 0x29, 0x19,
        0x15, 0x00, 0x25, 0x01,
        0x75, 0x01, 0x95, 0x19, 0x81, 0x02,
        0x75, 0x01, 0x95, 0x07, 0x81, 0x03,
        0xC0,  # End joystick Application
    ])
    return original[:6] + b'\x85\x01' + original[6:-1] + PID_DESCRIPTOR + PID_BLOCK_FREE + original[-1:]


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
    return b'\x01' + struct.pack('<HBBBBI', x, z_gas, brake, y_gas, hat, buttons)


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


class PIDPassive:
    """Return PID feature handshake but NEVER forward force to a physical motor."""
    def __init__(self):
        self.last_block = 1
        self.next_block = 1
        self.pending_kinds = {}
        self.output_counts = Counter()

    def seen(self, packet):
        if packet:
            self.output_counts[packet[0]] += 1

    def set_report(self, packet, number):
        if number == 0x54:  # Create New Effect, host feature report.
            self.last_block = self.next_block
            self.next_block = 1 + self.next_block % 40
            if len(packet) >= 2:
                self.pending_kinds[self.last_block] = packet[1]

    def get_report(self, number):
        if number == 0x56:  # PID Block Load (id, index, success, RAM available)
            return bytes((0x56, self.last_block, 1, 0xFF, 0xFF))
        if number == 0x57:  # PID Pool (id, RAM size, simultaneous max, flags)
            return bytes((0x57, 0xFF, 0xFF, 40, 1))
        return bytes([number])  # Minimal non-blocking reply to other GETs.


def respond_to_kernel(fd, event, ffb_monitor=None, pid=None):
    if len(event) < 4:
        return
    event_type = struct.unpack_from('<I', event)[0]
    active = ffb_monitor and (ffb_monitor.enabled or ffb_monitor.capture_file is not None)
    if event_type == UHID_OUTPUT and len(event) >= 4 + UHID_DATA_MAX + 3:
        size = struct.unpack_from('<H', event, 4 + UHID_DATA_MAX)[0]
        rtype = event[4 + UHID_DATA_MAX + 2]
        packet = event[4:4 + min(size, UHID_DATA_MAX)]
        if pid:
            pid.seen(packet)
        if active:
            ffb_monitor.capture('OUTPUT', packet, report_type=rtype)
            ffb_monitor.log('OUTPUT', f'type={rtype} len={size} bytes={packet[:40].hex(" ")}')
    elif event_type == UHID_SET_REPORT and len(event) >= 12:
        request_id = struct.unpack_from('<I', event, 4)[0]
        report_num, report_type = event[8], event[9]
        size = struct.unpack_from('<H', event, 10)[0]
        packet = event[12:12 + min(size, UHID_DATA_MAX)]
        if pid:
            pid.set_report(packet, report_num)
        if active:
            ffb_monitor.capture('SET_REPORT', packet, report_type=report_type, report_num=report_num)
            ffb_monitor.log('SET_REPORT', f'num=0x{report_num:02x} type={report_type} bytes={packet[:40].hex(" ")}')
        # Feature reports must be acknowledged, even in passive mode.
        os.write(fd, struct.pack('<IIH', UHID_SET_REPORT_REPLY, request_id, 0))
    elif event_type == UHID_GET_REPORT and len(event) >= 10:
        request_id = struct.unpack_from('<I', event, 4)[0]
        report_num, report_type = event[8], event[9]
        reply = pid.get_report(report_num) if pid else bytes([report_num])
        if active:
            ffb_monitor.capture('GET_REPORT', reply, report_type=report_type, report_num=report_num)
            ffb_monitor.log('GET_REPORT', f'num=0x{report_num:02x} type={report_type} -> {reply.hex(" ")}')
        os.write(fd, struct.pack('<IIHH', UHID_GET_REPORT_REPLY,
                                 request_id, 0, len(reply)) + reply)
    elif active and event_type == 4:
        ffb_monitor.log('OPEN', 'Interface HID aberta')
    elif active and event_type == 5:
        ffb_monitor.log('CLOSE', 'Interface HID fechada')


def hid_wheel_nodes(product_pid):
    return set(glob.glob(f'/sys/bus/hid/devices/0003:046D:{product_pid:04X}.*'))


def bind_virtual_g29(before, fd, pid, monitor):
    """Bind asynchronously: HID PID driver may synchronously issue feature GETs.

    If /sys/.../bind is written on the same thread that handles /dev/uhid,
    the kernel can wait for GET_REPORT replies while our thread blocks in bind.
    We therefore service UHID requests while a short-lived bind worker runs.
    """
    param = '/sys/module/hid/parameters/ignore_special_drivers'
    bind = '/sys/bus/hid/drivers/hid-generic/bind'
    target = None
    for _ in range(150):
        created = hid_wheel_nodes(0xc24f) - before
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
    original = open(param, 'rb').read().strip()
    result = {'error': None}
    def worker():
        try:
            try:
                with open(param, 'wb') as f:
                    f.write(b'1\n')
                with open(bind, 'wb') as f:
                    f.write(os.path.basename(target).encode('ascii') + b'\n')
            finally:
                with open(param, 'wb') as f:
                    f.write(original + b'\n')
        except Exception as exc:
            result['error'] = exc
    t = threading.Thread(target=worker, daemon=True, name='bind-g29-hid-generic')
    t.start()
    deadline = time.monotonic() + 12
    poller = select.poll()
    poller.register(fd, select.POLLIN)
    while t.is_alive() and time.monotonic() < deadline:
        for ready_fd, mask in poller.poll(100):
            if ready_fd == fd and mask & select.POLLIN:
                while True:
                    try:
                        event = os.read(fd, 4380)
                    except BlockingIOError:
                        break
                    if not event:
                        break
                    respond_to_kernel(fd, event, monitor, pid)
    t.join(timeout=0.5)
    if t.is_alive():
        # Fail-safe: never leave a GLOBAL special-driver override active.
        with open(param, 'wb') as f:
            f.write(original + b'\n')
        raise RuntimeError('Bind HID timeout (parâmetro pode ainda estar sendo restaurado): '
                           'não abra o jogo; confira ignore_special_drivers como root')
    if result['error']:
        raise RuntimeError('Falha no bind do G29: ' + str(result['error']))
    link = os.path.join(target, 'driver')
    if not os.path.islink(link) or not os.path.realpath(link).endswith('/hid-generic'):
        raise RuntimeError('Falha ao associar hid-generic ao G29 UHID')
    print('G29 virtual associado a hid-generic; parâmetro global restaurado.', flush=True)



def bind_virtual_pidff(before, fd, pid_state, monitor, product_pid, registration):
    """Bind only the newly created UHID G29/G920 to universal PIDFF.

    The G920 product ID normally auto-binds to logitech-hidpp-device.
    Unbind that *virtual* node before registering the PIDFF dynamic ID.
    Never unbind a physical wheel or touch any other VID/PID.
    Respond to synchronous kernel PID requests while registering new_id.
    """
    import subprocess
    driver_root = '/sys/bus/hid/drivers/hid-universal-pidff'
    subprocess.run(['modprobe', 'hid-universal-pidff'], check=True)
    new_id = driver_root + '/new_id'
    if not os.path.isfile(new_id):
        raise RuntimeError('Driver hid-universal-pidff não expõe new_id neste kernel')

    target = None
    for _ in range(150):
        new = hid_wheel_nodes(product_pid) - before
        virtual = [p for p in new if '/virtual/misc/uhid/' in os.path.realpath(p)]
        if len(virtual) == 1:
            target = virtual[0]
            break
        if len(virtual) > 1:
            raise RuntimeError('Mais de um volante virtual novo; abortando')
        time.sleep(0.02)
    if target is None:
        raise RuntimeError('Dispositivo UHID G29/G920 não apareceu no sysfs')
    if '/virtual/misc/uhid/' not in os.path.realpath(target):
        raise RuntimeError('Proteção: alvo NÃO é UHID virtual; abortando')
    all_same_id = hid_wheel_nodes(product_pid)
    if len(all_same_id) != 1 or target not in all_same_id:
        raise RuntimeError('Há outro dispositivo com o mesmo VID/PID; abortando')

    link = target + '/driver'
    if os.path.islink(link):
        bound = os.path.realpath(link)
        if bound.endswith('/hid-universal-pidff'):
            print('Volante virtual já associado a hid-universal-pidff.', flush=True)
            return
        if not bound.endswith('/logitech-hidpp-device'):
            raise RuntimeError('Driver inesperado no volante virtual; não desassociar: ' + bound)
        # The sysfs unbind target is the exact HID node created *by us*.
        # Validation immediately before writing protects the physical G920.
        if '/virtual/misc/uhid/' not in os.path.realpath(target) or len(hid_wheel_nodes(product_pid)) != 1:
            raise RuntimeError('Proteção de dispositivo falhou antes do unbind')
        with open(bound + '/unbind', 'wb') as out:
            out.write((os.path.basename(target) + '\n').encode('ascii'))
        if os.path.islink(link):
            raise RuntimeError('Volante virtual ainda está associado após unbind')
        print('[PIDFF] logitech-hidpp-device desassociado SOMENTE do G920 virtual.', flush=True)

    result = {'error': None}
    def worker():
        try:
            with open(new_id, 'wb') as out:
                out.write(f'0003 0000046D {product_pid:08X}\n'.encode('ascii'))
            registration['registered'] = True
        except Exception as exc:
            # The kernel may have registered the ID before reporting an error.
            registration['uncertain'] = True
            result['error'] = exc
    thread = threading.Thread(target=worker, daemon=True, name='bind-virtual-universal-pidff')
    thread.start()
    poller = select.poll()
    poller.register(fd, select.POLLIN)
    deadline = time.monotonic() + 25
    while thread.is_alive() and time.monotonic() < deadline:
        for ready_fd, mask in poller.poll(80):
            if ready_fd != fd or not (mask & select.POLLIN):
                continue
            while True:
                try:
                    event = os.read(fd, 4380)
                except BlockingIOError:
                    break
                if not event:
                    break
                respond_to_kernel(fd, event, monitor, pid_state)
    if thread.is_alive():
        registration['uncertain'] = True
        raise RuntimeError('Timeout ao registrar PIDFF; não abra o jogo. Consulte dmesg.')
    if result['error'] is not None:
        raise RuntimeError('Falha ao registrar ID dinâmico PIDFF: ' + str(result['error']))
    if not os.path.islink(link):
        raise RuntimeError('new_id registrado, mas nenhum driver associado; confira dmesg')
    actual = os.path.realpath(link)
    if not actual.endswith('/hid-universal-pidff'):
        raise RuntimeError('Driver inesperado no volante virtual: ' + actual)
    print('G29/G920 UHID associado a hid-universal-pidff (teste passivo).', flush=True)
    print('FFB físico desativado; Ctrl+C encerra e tenta limpar o ID temporário.', flush=True)


def cleanup_pidff(product_pid, registration):
    """Remove the dynamic ID if available, or unload an unused PIDFF module.

    No touching other drivers, other bound devices, or global HID settings.
    The module cannot always be unloaded, in which case reboot is required.
    """
    if not registration['registered'] and not registration['uncertain']:
        return
    import subprocess
    driver_root = '/sys/bus/hid/drivers/hid-universal-pidff'
    remove_id = driver_root + '/remove_id'
    if os.path.isfile(remove_id):
        try:
            with open(remove_id, 'wb') as out:
                out.write(f'0003 0000046D {product_pid:08X}\n'.encode('ascii'))
            print(f'[PIDFF] ID temporário 046d:{product_pid:04x} removido.', flush=True)
            return
        except OSError as exc:
            print(f'[PIDFF] remove_id falhou ({exc}); tentando descarregar o módulo.', flush=True)
    # Safety: never unload a module actively serving another HID device.
    if os.path.isdir(driver_root):
        attached = [name for name in os.listdir(driver_root)
                    if name.startswith(('0003:', '0005:', '0004:'))
                    and os.path.islink(os.path.join(driver_root, name))]
        if attached:
            print('[PIDFF] Outros dispositivos utilizam o driver: ' + ', '.join(attached), flush=True)
            print('[PIDFF] Reinicie ANTES de reconectar o G920 físico.', flush=True)
            return
    completed = subprocess.run(['modprobe', '-r', 'hid-universal-pidff'],
                               capture_output=True, text=True)
    if completed.returncode == 0:
        print('[PIDFF] Módulo descarregado; IDs dinâmicos limpos.', flush=True)
    else:
        print('[PIDFF] Não foi possível descarregar módulo: ' + completed.stderr.strip(), flush=True)
        print('[PIDFF] Reinicie ANTES de reconectar o G920 físico.', flush=True)


def run(args):
    if args.auto_bind and args.auto_pidff:
        raise ValueError('Use apenas --auto-bind OU --auto-pidff')
    if args.auto_pidff and args.identity not in ('g29', 'g920'):
        raise ValueError('--auto-pidff exige --identity g29 ou g920')
    if args.auto_bind and args.identity != 'g29':
        raise ValueError('--auto-bind só é suportado com --identity g29')
    source = InputDevice(SOURCE)
    fd = -1
    grabbed = False
    pid_state = PIDPassive()
    pidff_registration = {'registered': False, 'uncertain': False}
    ffb_monitor = FFBMonitor(args.ffb_monitor, args.ffb_capture)
    try:
        info_x = source.absinfo(e.ABS_X)
        steer = info_x.value
        accel = source.absinfo(e.ABS_Y).value
        brake = source.absinfo(e.ABS_Z).value
        hatx, haty = 0, 0
        pressed = set()
        identities = {
            'g29': (0x046d, 0xc24f, 'Logitech G29 Driving Force Racing Wheel'),
            'g920': (0x046d, 0xc262, 'Logitech G920 Driving Force Racing Wheel'),
            'generic': (0x1209, 0xd029, 'DFGT UHID Steering Wheel Probe'),
        }
        vid, product_pid, name = identities[args.identity]
        same_id_before = hid_wheel_nodes(product_pid) if args.auto_pidff or args.auto_bind else None
        if args.identity == 'g920' and same_id_before:
            raise RuntimeError('Há um G920 046d:c262 conectado ou virtual ainda ativo. '
                               'Desconecte o G920 físico e encerre instâncias antigas antes do teste.')
        fd = os.open('/dev/uhid', os.O_RDWR | os.O_CLOEXEC | os.O_NONBLOCK)
        create_device(fd, name, vid, product_pid)
        if args.auto_pidff:
            bind_virtual_pidff(same_id_before, fd, pid_state, ffb_monitor, product_pid, pidff_registration)
        elif args.auto_bind:
            bind_virtual_g29(same_id_before, fd, pid_state, ffb_monitor)
        if args.grab:
            source.grab()
            grabbed = True
        print(f'DFGT físico: {source.name} (046d:c29a)')
        print(f'UHID virtual: {name} ({vid:04x}:{product_pid:04x})')
        print(f'HID: direção X16, acelerador {args.gas_axis.upper()}8, freio Rz8, hat, 25 botões')
        print(f'Acelerador invertido: {"SIM" if args.invert_gas else "NÃO"}')
        print('HID PID v0.7: RELATÓRIOS DE FORCE FEEDBACK ATIVOS PARA CAPTURA.')
        print('FFB PASSIVO: nenhum comando é enviado ao motor do DFGT.')
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
                        respond_to_kernel(fd, data, ffb_monitor, pid_state)
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
        print('[PID] Relatórios OUTPUT vistos:',
              repr([(f"0x{k:02x}", v) for k, v in pid_state.output_counts.most_common(15)]), flush=True)
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
        if args.auto_pidff:
            cleanup_pidff(product_pid, pidff_registration)
        source.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='DFGT UHID G29/G920 v0.7-fix (hid-universal-pidff, FFB passivo)')
    parser.add_argument('--self-test', action='store_true', help='Testa descritor e relatórios, sem UHID nem sudo')
    parser.add_argument('--identity', choices=['generic', 'g29', 'g920'], default='g29',
                        help='Identidade virtual; g920 experimental (G920 físico deve estar desconectado)')
    parser.add_argument('--grab', action='store_true',
                        help='Oculta eventos evdev físicos de outros leitores durante o teste')
    parser.add_argument('--monitor', action='store_true')
    parser.add_argument('--ffb-monitor', action='store_true',
                        help='Mostra relatórios PID enviados pelo Proton; NÃO aciona motor')
    parser.add_argument('--ffb-capture', metavar='ARQUIVO_CSV',
                        help='Grava OUTPUT/GET_REPORT/SET_REPORT incluindo PID; não aciona motor')
    parser.add_argument('--auto-bind', action='store_true',
                        help='Associe o G29 virtual ao hid-generic (modo antigo)')
    parser.add_argument('--auto-pidff', action='store_true',
                        help='Tente associar o G29/G920 virtual ao hid-universal-pidff, sem motor físico')
    parser.add_argument('--gas-axis', choices=['z', 'y', 'both'], default='z',
                        help='Onde colocar o acelerador no HID (padrão z)')
    parser.add_argument('--invert-gas', action='store_true',
                        help='Inverte somente o acelerador (teste)')
    args = parser.parse_args()
    if args.self_test:
        d = descriptor()
        expected = [0x01, 0x50, 0x51, 0x54, 0x55, 0x56, 0x57, 0x59, 0x5A, 0x5B]
        for rid in expected:
            assert bytes([0x85, rid]) in d, f'Report ID ausente: 0x{rid:02x}'
        state = (8192, 255, 255, 0, 0, set())
        r = report(state, 'y')
        assert len(r) == 11 and r[0] == 1, (len(r), r.hex())
        assert r[3] == 255 and r[4] == 255 and r[5] == 255 and r[6] == 8, r.hex()
        assert len(d) < UHID_DATA_MAX
        assert PIDPassive().get_report(0x57) == bytes((0x57, 255, 255, 40, 1))
        # Regression: in v0.5, product PID shadowed the PIDPassive object.
        state_pid = PIDPassive()
        read_fd, write_fd = os.pipe()
        try:
            # Mock UHID_OUTPUT containing a PID Device Gain report.
            packet = bytes((0x59, 0x7f))
            fake_output = (struct.pack('<I', UHID_OUTPUT)
                           + packet.ljust(UHID_DATA_MAX, bytes(1))
                           + struct.pack('<HB', len(packet), 1))
            respond_to_kernel(write_fd, fake_output, None, state_pid)
            assert state_pid.output_counts[0x59] == 1
            # Mock UHID_GET_REPORT for PID Pool (0x57).
            fake_get = struct.pack('<IIBB', UHID_GET_REPORT, 123, 0x57, 3)
            respond_to_kernel(write_fd, fake_get, None, state_pid)
            reply = os.read(read_fd, 32)
            assert struct.unpack_from('<I', reply)[0] == UHID_GET_REPORT_REPLY
            assert struct.unpack_from('<I', reply, 4)[0] == 123
            assert reply[12:] == state_pid.get_report(0x57)
            # Mock UHID_SET_REPORT for Create New Effect (0x54).
            set_data = bytes((0x54, 0x01))
            fake_set = struct.pack('<IIBBH', UHID_SET_REPORT, 124, 0x54, 3, len(set_data)) + set_data
            respond_to_kernel(write_fd, fake_set, None, state_pid)
            set_reply = os.read(read_fd, 32)
            assert struct.unpack_from('<I', set_reply)[0] == UHID_SET_REPORT_REPLY
            assert struct.unpack_from('<I', set_reply, 4)[0] == 124
            assert state_pid.pending_kinds
        finally:
            os.close(read_fd)
            os.close(write_fd)
        assert callable(bind_virtual_pidff) and callable(cleanup_pidff)
        assert len(hid_wheel_nodes(0xc262)) >= 0
        print(f'Autoteste v0.7-fix OK: HID descriptor={len(d)} bytes, joystick report={len(r)} bytes')
    else:
        try:
            run(args)
        except KeyboardInterrupt:
            print('\nUHID virtual encerrado.')
        except PermissionError as exc:
            parser.error(f'{exc}. /dev/uhid é root-only: teste com sudo.')
