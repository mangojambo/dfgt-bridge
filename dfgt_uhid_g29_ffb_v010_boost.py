#!/usr/bin/env python3
"""DFGT -> UHID G29 with opt-in, boosted & capped EVDEV constant FFB bridge v0.10.

Copyright (c) 2026. SPDX-License-Identifier: GPL-2.0-only.
HID PID descriptor adapted from mescon/logitech-trueforce-linux-driver,
userspace/logi-wheel/crates/logi-ffb/src/descriptor.rs (GPL-2.0-only):
https://github.com/mescon/logitech-trueforce-linux-driver


Experimental proof of concept. Creates a virtual HID device (not complete G29 hardware).
PASSIVE by default. --enable-ffb enables capped, short-lived physical FF_CONSTANT only.
Requires direct supervision; exit with Ctrl+C to stop and remove the virtual device.
No physical G920 may be connected during FFB tests.

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
from evdev import InputDevice, ecodes as e, ff, list_devices

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
    # v0.8: Add Automobile Simulation Device as a nested HID logical
    # collection around the *original* axes. Wine checks this collection
    # usage to classify the DirectInput device as DI8DEVTYPE_DRIVING.
    # No Input/Output/Feature fields are added, and not a single input bit
    # changes position. v0.6 remains available for immediate rollback.
    sim_car = bytes((0x05, 0x02,  # Usage Page: Simulation Controls
                     0x09, 0x02,  # Usage: Automobile Simulation Device
                     0xA1, 0x02,  # Collection: Logical (nested in Joystick)
                     0x05, 0x01)) # Restore Generic Desktop axes page
    layout = original[6:-1]
    axis_collection_end = bytes((0xC0, 0x09, 0x39))
    pos = layout.find(axis_collection_end)
    assert pos >= 0, "Missing physical axes end + Hat Switch"
    # Place automobile collection around the existing pointer collection.
    # We close it immediately after the pointer collection, so the hat and
    # buttons (and all PID reports) retain the same scope as in v0.6.
    layout = sim_car + layout[:pos + 1] + b'\xC0' + layout[pos + 1:]
    return original[:6] + b'\x85\x01' + layout + PID_DESCRIPTOR + PID_BLOCK_FREE + original[-1:]


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




# Opt-in physical FFB bridge. Intentionally only supports PID Constant Force:
# the much more frequent PID condition (spring/damper/friction) and periodic
# effects are logged but NOT translated. Those need separate characterization.
# Every motor write is bounded by a short hardware-side replay duration, a
# modest amplitude ceiling, and a fixed rate limit. An idle game cannot leave
# the motor energized: the real wheel stops automatically when replay expires.
class PIDConstantBridge(PIDPassive):
    def __init__(self, clock=time.monotonic):
        super().__init__()
        self.clock = clock
        self.effects = {}
        self.gain = 1.0
        self.armed = False
        self.paused = False
        self.last_command_time = -1e9
        self.pid_counts = Counter()

    def arm(self):
        # The universal-pidff driver's own attach-time probe has completed.
        self.effects.clear()
        self.gain = 1.0
        self.paused = False
        self.last_command_time = -1e9
        self.armed = True

    def set_report(self, packet, number):
        super().set_report(packet, number)
        if not self.armed:
            return
        if number == 0x54 and len(packet) >= 2:
            kind = packet[1]  # 1 is constant in the descriptor's usage list.
            block = self.last_block
            self.effects[block] = dict(kind=kind, level=0, running=False, gain=1.0)
            self.pid_counts['created'] += 1
        elif number == 0x5b and len(packet) >= 2:
            self.effects.pop(packet[1], None)
            self.pid_counts['deleted'] += 1
        elif number == 0x59 and len(packet) >= 2:
            self.gain = packet[1] / 255.0

    def seen(self, packet):
        super().seen(packet)
        if not self.armed or not packet:
            return
        opcode = packet[0]
        self.pid_counts[f'0x{opcode:02x}'] += 1
        if opcode == 0x50 and len(packet) >= 2:
            ctrl = packet[1]
            if ctrl == 4:  # Device Reset
                self.effects.clear()
                self.last_command_time = -1e9
                self.paused = False
            elif ctrl in (2, 3, 5):  # Disable, Stop All, Pause
                for item in self.effects.values():
                    item['running'] = False
                self.last_command_time = -1e9
                if ctrl in (2, 5):
                    self.paused = True
            elif ctrl in (1, 6):  # Enable, Continue
                self.paused = False
            return
        if opcode == 0x59 and len(packet) >= 2:
            self.gain = packet[1] / 255.0
            return
        if len(packet) < 2:
            return
        block = packet[1]
        item = self.effects.get(block)
        if item is None:
            return
        if opcode == 0x51 and len(packet) >= 18:
            # PID Set Effect's gain field declares 0..100 (byte 11).
            item['gain'] = min(1.0, packet[11] / 100.0)
        elif opcode == 0x55 and len(packet) >= 4 and item['kind'] == 1:
            item['level'] = struct.unpack_from('<h', packet, 2)[0]
            if item['running']:
                self.last_command_time = self.clock()
        elif opcode == 0x5a and len(packet) >= 4:
            op = packet[2]
            if op in (1, 2):
                if op == 2:  # start solo: stop other blocks
                    for other in self.effects.values():
                        other['running'] = False
                item['running'] = True
                if item['kind'] == 1:
                    self.last_command_time = self.clock()
            elif op == 3:
                item['running'] = False

    def raw_level(self, max_age_s=0.2):
        if not self.armed or self.paused or self.clock() - self.last_command_time > max_age_s:
            return 0
        val = sum(int(item['level'] * item['gain'])
                  for item in self.effects.values()
                  if item['kind'] == 1 and item['running'])
        return max(-32767, min(32767, round(val * self.gain)))


class PhysicalConstantMotor:
    """One effect slot, ≤20 updates/s, 160ms device-side expiry, amplitude cap."""
    def __init__(self, dev, strength_percent=12, rate_hz=20, clock=time.monotonic, boost=1.0):
        if (dev.info.vendor, dev.info.product) != (0x046d, 0xc29a):
            raise RuntimeError('FFB rejeitado: fonte física não é DFGT 046d:c29a')
        if e.FF_CONSTANT not in dev.capabilities().get(e.EV_FF, []):
            raise RuntimeError('FFB rejeitado: DFGT físico não suporta FF_CONSTANT')
        if not (5 <= strength_percent <= 20 and 10 <= rate_hz <= 20):
            raise ValueError('Limites de segurança: força 5..20%, frequência 10..20 Hz')
        import math
        if not math.isfinite(boost) or not 1.0 <= boost <= 12.0:
            raise ValueError('Boost precisa estar entre 1.0 e 12.0')
        self.dev = dev
        self.clock = clock
        self.boost = boost
        self.cap = round(32767 * strength_percent / 100)
        self.interval = 1.0 / rate_hz
        self.id = -1
        self.last_tick = -1e9
        self.last_sent = 0
        self.playing = False
        self.writes = 0
        self.peak = 0
        self.peak_input = 0
        self.saturation_ticks = 0
        self.deadline_ms = 160  # Real effect stops without a refreshed play request.
        self.first_active = None
        self.expired = False
        self.test_limit_s = 90  # One supervised first-test session only.

    def _effect(self, level):
        return ff.Effect(
            e.FF_CONSTANT, self.id, 0x4000,
            ff.Trigger(0, 0), ff.Replay(self.deadline_ms, 0),
            ff.EffectType(ff_constant_effect=ff.Constant(
                level, ff.Envelope(0, 0, 0, 0))))

    def tick(self, normalized_level):
        now = self.clock()
        if now - self.last_tick < self.interval:
            return
        self.last_tick = now
        if self.expired or (self.first_active is not None and
                            now - self.first_active >= self.test_limit_s):
            if not self.expired:
                print('[FFB físico] Limite de teste de 90s atingido; motor parado. '
                      'Encerre e reinicie o script para novo teste.', flush=True)
            self.expired = True
            self.stop()
            return
        # Boost applies BEFORE physical cap. At 10% game output, boost 6x
        # requests 60% of the chosen physical cap, NOT 60% motor power.
        # Never forward raw unbounded game magnitude to the physical DFGT.
        self.peak_input = max(self.peak_input, abs(normalized_level))
        requested = round(normalized_level * self.boost * self.cap / 32767)
        if abs(requested) > self.cap:
            self.saturation_ticks += 1
        level = max(-self.cap, min(self.cap, requested))
        if level == 0:
            self.stop()  # Stop immediately on reset / watchdog / no effect.
            return
        if self.first_active is None:
            self.first_active = now
        # Limit change in output per tick; avoids a sudden full reversal.
        step = max(512, self.cap // 3)
        if level > self.last_sent + step:
            level = self.last_sent + step
        elif level < self.last_sent - step:
            level = self.last_sent - step
        if level == 0:
            self.stop()
            return
        new_id = self.dev.upload_effect(self._effect(level))
        if self.id >= 0 and new_id != self.id:
            raise RuntimeError('Driver não reutilizou slot FFB; encerrando por segurança')
        self.id = new_id
        self.dev.write(e.EV_FF, self.id, 1)
        self.dev.syn()
        self.playing = True
        self.last_sent = level
        self.peak = max(self.peak, abs(level))
        self.writes += 1

    def stop(self):
        # No repeated zero commands: avoid burdening the Logitech driver.
        if self.playing and self.id >= 0:
            self.dev.write(e.EV_FF, self.id, 0)
            self.dev.syn()
        self.playing = False
        self.last_sent = 0

    def close(self):
        try:
            self.stop()
        finally:
            if self.id >= 0:
                try:
                    self.dev.erase_effect(self.id)
                finally:
                    self.id = -1


def ensure_g920_unplugged():
    """The G920 physical and the G29 emulated device must not be confused."""
    for path in list_devices():
        device = InputDevice(path)
        try:
            if (device.info.vendor, device.info.product) == (0x046d, 0xc262):
                raise RuntimeError('Desconecte o G920 físico antes de habilitar FFB do DFGT')
        finally:
            device.close()


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


def hid_g29_nodes():
    return set(glob.glob('/sys/bus/hid/devices/0003:046D:C24F.*'))


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



def bind_virtual_pidff(before, fd, pid_state, monitor):
    """Experimental, non-persistent HID dynamic ID for the virtual G29 only.

    sysfs new_id triggers a synchronous driver_attach(), during which the
    kernel might issue GET_REPORT and wait for this process to acknowledge.
    Handle those requests from the main thread while a worker writes new_id.
    Does not touch the physical DFGT motor, G920, or global HID parameters.
    """
    import subprocess
    driver_root = '/sys/bus/hid/drivers/hid-universal-pidff'
    subprocess.run(['modprobe', 'hid-universal-pidff'], check=True)
    new_id = driver_root + '/new_id'
    if not os.path.isfile(new_id):
        raise RuntimeError('Driver hid-universal-pidff não expõe new_id neste kernel')
    target = None
    for _ in range(150):
        new = hid_g29_nodes() - before
        virtual = [p for p in new if '/virtual/misc/uhid/' in os.path.realpath(p)]
        if len(virtual) == 1:
            target = virtual[0]
            break
        if len(virtual) > 1:
            raise RuntimeError('Mais de um G29 virtual novo; abortando')
        time.sleep(0.02)
    if target is None:
        raise RuntimeError('Dispositivo UHID G29 não apareceu no sysfs')
    all_g29 = hid_g29_nodes()
    if len(all_g29) != 1:
        raise RuntimeError('Há outros G29 046d:c24f presentes; por segurança não registrar new_id')
    if os.path.islink(target + '/driver'):
        bound = os.path.realpath(target + '/driver')
        if bound.endswith('/hid-universal-pidff'):
            print('G29 já está associado a hid-universal-pidff.', flush=True)
            return
        raise RuntimeError('G29 virtual já possui outro driver: ' + bound)
    result = {'error': None}
    def worker():
        try:
            # Dynamic ID matches bus 0003, vendor 046D, product C24F.
            # Driver's sysfs handler probes unbound matching devices.
            with open(new_id, 'wb') as out:
                out.write(b'0003 0000046D 0000C24F\n')
        except Exception as exc:
            result['error'] = exc
    thread = threading.Thread(target=worker, daemon=True, name='bind-g29-universal-pidff')
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
        raise RuntimeError('Timeout ao registrar o PIDFF; não abra o jogo. '
                           'Encerre o script e confira dmesg.')
    if result['error'] is not None:
        raise RuntimeError('Falha ao registrar ID dinâmico PIDFF: ' + str(result['error']))
    driver_link = target + '/driver'
    if not os.path.islink(driver_link):
        raise RuntimeError('new_id registrado, mas nenhum driver associado. '
                           'Consulte: sudo dmesg | tail -n 50')
    actual = os.path.realpath(driver_link)
    if not actual.endswith('/hid-universal-pidff'):
        raise RuntimeError('Driver inesperado no G29 virtual: ' + actual)
    print('G29 UHID associado a hid-universal-pidff (teste passivo).', flush=True)
    print('ID dinâmico vale até descarregar o módulo ou reiniciar; '
          'nenhuma configuração persistente foi criada.', flush=True)


def run(args):
    if args.auto_bind and args.auto_pidff:
        raise ValueError('Use apenas --auto-bind OU --auto-pidff')
    if args.auto_pidff and args.identity != 'g29':
        raise ValueError('--auto-pidff exige --identity g29')
    source = InputDevice(SOURCE)
    # The bridge must never accidentally target the G29 UHID or a G920.
    if args.enable_ffb:
        if not args.auto_pidff or args.identity != 'g29':
            source.close()
            raise ValueError('--enable-ffb exige --auto-pidff --identity g29')
        if (source.info.vendor, source.info.product) != (0x046d, 0xc29a):
            source.close()
            raise RuntimeError('Fonte não é DFGT 046d:c29a; FFB abortado')
        try:
            ensure_g920_unplugged()
        except Exception:
            source.close()
            raise
    fd = -1
    grabbed = False
    pid_state = PIDConstantBridge()
    ffb_monitor = FFBMonitor(args.ffb_monitor, args.ffb_capture)
    motor = None
    try:
        info_x = source.absinfo(e.ABS_X)
        steer = info_x.value
        accel = source.absinfo(e.ABS_Y).value
        brake = source.absinfo(e.ABS_Z).value
        hatx, haty = 0, 0
        pressed = set()
        vid, product_pid = (0x046d, 0xc24f) if args.identity == 'g29' else (0x1209, 0xd029)
        name = ('Logitech G29 Driving Force Racing Wheel' if args.identity == 'g29'
                else 'DFGT UHID Steering Wheel Probe')
        g29_before = hid_g29_nodes() if (args.auto_bind or args.auto_pidff) and args.identity == 'g29' else None
        fd = os.open('/dev/uhid', os.O_RDWR | os.O_CLOEXEC | os.O_NONBLOCK)
        create_device(fd, name, vid, product_pid)
        if args.auto_pidff:
            bind_virtual_pidff(g29_before, fd, pid_state, ffb_monitor)
        elif g29_before is not None:
            bind_virtual_g29(g29_before, fd, pid_state, ffb_monitor)
        if args.enable_ffb:
            motor = PhysicalConstantMotor(source, args.ffb_strength, 20, boost=args.ffb_boost)
            pid_state.arm()
        if args.grab:
            source.grab()
            grabbed = True
        print(f'DFGT físico: {source.name} (046d:c29a)')
        print(f'UHID virtual: {name} ({vid:04x}:{product_pid:04x})')
        print(f'HID: direção X16, acelerador {args.gas_axis.upper()}8, freio Rz8, hat, 25 botões')
        print(f'Acelerador invertido: {"SIM" if args.invert_gas else "NÃO"}')
        print('HID PID v0.10: classificação Automóvel / DirectInput; controles da v0.8 preservados.')
        if motor:
            print(f'FFB FÍSICO ATIVO: somente CONSTANT, teto {args.ffb_strength}%, '
                  f'boost {args.ffb_boost:.1f}x, máximo 20 Hz, replay 160ms, watchdog 200ms, teste 90s.')
            print('Segure o aro; Ctrl+C ou encerramento para interromper o efeito.', flush=True)
        else:
            print('FFB PASSIVO por padrão; nenhum comando será enviado ao motor.')
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
            for ready_fd, _ in poller.poll(25 if motor else 250):
                if ready_fd == fd:
                    for _batch in range(64):
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
            if motor:
                motor.tick(pid_state.raw_level())
    finally:
        if motor:
            try:
                motor.close()
                print(f'[FFB físico] Parado/limpo. Atualizações={motor.writes}; '
                      f'pico aplicado={motor.peak}/32767 ({motor.peak/32767*100:.1f}%); '
                      f'pico sinal do jogo={motor.peak_input/32767*100:.1f}%; '
                      f'clippings={motor.saturation_ticks}.', flush=True)
                print('[FFB PID] Operações:', pid_state.pid_counts.most_common(12), flush=True)
            except Exception as exc:
                print('[AVISO] Falha ao limpar FFB físico:', exc, flush=True)
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
        source.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='DFGT UHID G29 v0.10 (FFB constante com boost e teto, opt-in)')
    parser.add_argument('--self-test', action='store_true', help='Testa descritor e relatórios, sem UHID nem sudo')
    parser.add_argument('--identity', choices=['generic', 'g29'], default='g29',
                        help='Identidade virtual; g29 é experimental (padrão)')
    parser.add_argument('--grab', action='store_true',
                        help='Oculta eventos evdev físicos de outros leitores durante o teste')
    parser.add_argument('--monitor', action='store_true')
    parser.add_argument('--ffb-monitor', action='store_true',
                        help='Mostra relatórios PID enviados pelo Proton (não habilita motor sozinho)')
    parser.add_argument('--ffb-capture', metavar='ARQUIVO_CSV',
                        help='Grava OUTPUT/GET_REPORT/SET_REPORT incluindo PID; não aciona motor')
    parser.add_argument('--auto-bind', action='store_true',
                        help='Associe o G29 virtual ao hid-generic (modo antigo)')
    parser.add_argument('--auto-pidff', action='store_true',
                        help='Associe G29 UHID ao hid-universal-pidff (FFB físico só com --enable-ffb)')
    parser.add_argument('--gas-axis', choices=['z', 'y', 'both'], default='z',
                        help='Onde colocar o acelerador no HID (padrão z)')
    parser.add_argument('--invert-gas', action='store_true',
                        help='Inverte somente o acelerador (teste)')
    parser.add_argument('--enable-ffb', action='store_true',
                        help='ENVIA força constante limitada ao motor DFGT; opcional e experimental')
    parser.add_argument('--ffb-strength', type=int, default=12,
                        help='Teto da força física (5..20%%); padrão 12')
    parser.add_argument('--ffb-boost', type=float, default=1.0,
                        help='Amplificação antes do teto (1..12x); padrão 1. Use com cautela.')
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
        assert bytes.fromhex('05020902a1020501') in d, 'Automobile Simulation Device collection missing'
        # Offline HID descriptor parser: check balanced collections and
        # exact input report size (the original G29 proxy has 10 data bytes).
        def hid_report_bits(buf):
            size_by_code = (0, 1, 2, 4)
            r_id, r_size, r_count = 0, 0, 0
            input_bits = Counter()
            col_depth = 0
            sim_collections = 0
            page, usage = 0, 0
            index = 0
            while index < len(buf):
                lead = buf[index]; index += 1
                if lead == 0xFE:
                    count = buf[index]; index += count + 2
                    continue
                length = size_by_code[lead & 3]
                value = int.from_bytes(buf[index:index + length], 'little')
                index += length
                tag, typ = (lead >> 4), ((lead >> 2) & 3)
                if typ == 1:  # Global
                    if tag == 0: page = value
                    elif tag == 7: r_size = value
                    elif tag == 8: r_id = value
                    elif tag == 9: r_count = value
                elif typ == 2: # Local
                    if tag == 0: usage = value
                elif typ == 0: # Main
                    if tag == 8: input_bits[r_id] += r_size * r_count
                    elif tag == 10:
                        col_depth += 1
                        if page == 0x02 and usage == 0x02:
                            sim_collections += 1
                    elif tag == 12:
                        col_depth -= 1
                        assert col_depth >= 0, 'Unbalanced HID collection'
            assert col_depth == 0, f'Unbalanced HID collections: {col_depth}'
            assert input_bits[1] == 80, f'Input layout changed: {input_bits}'
            assert sim_collections == 1, 'Expected exactly 1 car collection'
            return input_bits
        hid_report_bits(d)
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
        assert callable(bind_virtual_pidff)
        # Offline PID sample from the actual DiRT 5 capture: feature create,
        # magnitude, start, stop and watchdog. No hardware is touched.
        fake_now = [10.0]
        bridge = PIDConstantBridge(clock=lambda: fake_now[0])
        bridge.set_report(bytes((0x54, 1, 0, 0)), 0x54)
        bridge.arm()  # clears attach-time driver probe data
        bridge.set_report(bytes((0x54, 1, 0, 0)), 0x54)
        block = bridge.last_block
        bridge.seen(bytes((0x55, block, 0, 0x40)))  # 16384
        assert bridge.raw_level() == 0  # not started
        bridge.seen(bytes((0x5a, block, 1, 1)))
        assert bridge.raw_level() == 16384
        fake_now[0] += .201
        assert bridge.raw_level() == 0, 'Watchdog failed'
        bridge.seen(bytes((0x5a, block, 1, 1)))
        bridge.seen(bytes((0x55, block, 0, 0xC0)))  # -16384
        assert bridge.raw_level() == -16384
        bridge.seen(bytes((0x5a, block, 3, 0)))
        assert bridge.raw_level() == 0, 'Stop ignored'
        bridge.seen(bytes((0x5a, block, 1, 1)))
        bridge.seen(bytes((0x50, 4)))
        assert bridge.raw_level() == 0, 'Reset ignored'
        class FakeEvdev:
            class Info:
                vendor, product = 0x046d, 0xc29a
            info = Info()
            def __init__(self):
                self.actions = []
            def capabilities(self):
                return {e.EV_FF:[e.FF_CONSTANT]}
            def upload_effect(self, effect):
                self.actions.append(('upload', effect))
                return 7
            def write(self, typ, code, value):
                self.actions.append(('write', typ, code, value))
            def syn(self):
                pass
            def erase_effect(self, effect_id):
                self.actions.append(('erase', effect_id))
        mock = FakeEvdev()
        motor = PhysicalConstantMotor(mock, 12, 20, clock=lambda: fake_now[0])
        motor.tick(32767)
        assert motor.writes == 1 and 0 < motor.peak <= round(32767*.12)
        first_effect = next(action[1] for action in mock.actions if action[0] == 'upload')
        assert first_effect.args[4] == (160, 0), 'Hardware effect replay exceeds safety duration'
        fake_now[0] += .01
        motor.tick(32767)
        assert motor.writes == 1, 'Rate cap failed'
        fake_now[0] += .06
        motor.tick(0)
        assert not motor.playing, 'Motor stop failed'
        fake_now[0] += .06
        motor.tick(32767)
        assert motor.playing
        fake_now[0] += 90.01
        motor.tick(32767)
        assert not motor.playing and motor.expired, 'Timed kill switch did not stop'
        motor.close()
        assert ('erase', 7) in mock.actions
        # Regression for weak v0.9 forces: 10% input with boost 6, cap 16%
        # should become ~9.6% physical power without exceeding 16%.
        fake_now[0] += 5
        boosted = PhysicalConstantMotor(FakeEvdev(), 16, 20,
                                        clock=lambda: fake_now[0], boost=6)
        boosted.tick(3277)
        assert 0 < boosted.peak <= boosted.cap // 3 + 1, boosted.peak
        fake_now[0] += .06
        boosted.tick(3277)
        assert 2800 <= boosted.peak <= 3400, boosted.peak
        fake_now[0] += .1
        boosted.tick(32767)
        assert boosted.peak <= round(32767*.16)
        assert boosted.saturation_ticks >= 1
        boosted.close()
        print(f'Autoteste v0.10 OK: descriptor={len(d)} bytes, '
              f'relatório joystick={len(r)}; PID + rate cap + watchdog + limpeza OK')
    else:
        try:
            run(args)
        except KeyboardInterrupt:
            print('\nUHID virtual encerrado.')
        except PermissionError as exc:
            parser.error(f'{exc}. /dev/uhid é root-only: teste com sudo.')
