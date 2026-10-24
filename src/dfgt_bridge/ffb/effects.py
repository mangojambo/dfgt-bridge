# SPDX-License-Identifier: GPL-2.0-only
# Extraído da v0.13 preservada em reference/v0.13/.
"""Estado e renderização extraídos; divisão do decoder virá em etapa própria."""
from collections import Counter
import math
import struct
import time


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
        return bytes([number])


PID_EFFECT_NAMES = {
    1: 'constant', 2: 'ramp', 3: 'custom/unsupported',
    4: 'square', 5: 'sine', 6: 'triangle',
    7: 'saw_up', 8: 'saw_down', 9: 'spring',
    10: 'damper', 11: 'inertia', 12: 'friction'
}


OUTPUT_COMPONENTS = ('constant', 'spring', 'damper', 'periodic',
                     'friction', 'inertia', 'ramp')


class PIDSteeringBridge(PIDPassive):
    """Decode PID reports to per-effect state; render approximate single-axis torque.

    Coverage: all defined PID effect families except custom data (kind 3).
    Some PID semantics (two-axis conditions, envelope direction & multi-axis
    effect direction) cannot be faithfully reproduced on a DFGT single-axis
    EV_FF constant actuator; counters expose ignored reports explicitly.
    """
    def __init__(self, clock=time.monotonic):
        super().__init__()
        self.clock = clock
        self.effects = {}
        self.gain = 1.0
        self.armed = False
        self.paused = False
        self.last_command_time = -1e9
        self.pid_counts = Counter()
        self.created_by_kind = Counter()
        self.rendered_by_kind = Counter()
        self.ignored = Counter()
        self.spring_direction = -1
        self.spring_peak = 0
        self.spring_active_ticks = 0
        self.periodic_active_ticks = 0
        self.damper_active_ticks = 0
        self.last_physical_pos = None
        self.last_velocity_update = None
        self.smoothed_velocity = 0.0
        self.last_inertia_velocity = 0.0
        self.smoothed_acceleration = 0.0
        self.component_peaks = Counter()
        self.component_ticks = Counter()

    def arm(self):
        # Discard attach-time kernel probes; game effects start after this.
        self.effects.clear()
        self.gain = 1.0
        self.paused = False
        self.last_command_time = -1e9
        self.armed = True
        self.last_physical_pos = None
        self.last_velocity_update = None
        self.smoothed_velocity = 0.0
        self.smoothed_acceleration = 0.0

    def _new_effect(self, kind):
        return dict(kind=kind, level=0, running=False, gain=1.0,
                    center=0, coeff_pos=0, coeff_neg=0,
                    sat_pos=0, sat_neg=0, deadband=0,
                    condition_axis=-1, periodic_magnitude=0,
                    periodic_offset=0, periodic_phase=0, period_ms=0,
                    ramp_start=0, ramp_end=0, duration_ms=None,
                    start_delay_ms=0, direction=0,
                    attack_level=0, attack_time=0, fade_level=0,
                    fade_time=0, started_at=None)

    def set_report(self, packet, number):
        super().set_report(packet, number)
        if not self.armed:
            return
        if number == 0x54 and len(packet) >= 2:
            kind = packet[1]
            block = self.last_block
            self.effects[block] = self._new_effect(kind)
            self.created_by_kind[PID_EFFECT_NAMES.get(kind, f'unknown-{kind}')] += 1
            self.pid_counts['created'] += 1
            if kind == 3 or kind not in PID_EFFECT_NAMES:
                self.ignored[f'unsupported_kind_{kind}'] += 1
        elif number == 0x5b and len(packet) >= 2:
            self.effects.pop(packet[1], None)
            self.pid_counts['deleted'] += 1
        elif number == 0x59 and len(packet) >= 2:
            self.gain = packet[1] / 255.0

    def seen(self, packet):
        super().seen(packet)
        if not self.armed or not packet:
            return
        op = packet[0]
        self.pid_counts[f'0x{op:02x}'] += 1
        now = self.clock()
        if op == 0x50 and len(packet) >= 2:
            ctrl = packet[1]
            if ctrl == 4:  # device reset
                self.effects.clear()
                self.last_command_time = -1e9
                self.paused = False
            elif ctrl in (2, 3, 5):
                for item in self.effects.values():
                    item['running'] = False
                self.last_command_time = -1e9
                if ctrl in (2, 5): self.paused = True
            elif ctrl in (1, 6):
                self.paused = False
            return
        if op == 0x59 and len(packet) >= 2:
            self.gain = packet[1] / 255.0
            return
        if len(packet) < 2:
            self.ignored['short_packet'] += 1
            return
        block = packet[1]
        item = self.effects.get(block)
        if item is None:
            # Unknown vendor commands (0x0D, 0xF5, 0xF8) are kept out of FFB.
            if op in (0x51, 0x52, 0x53, 0x55, 0x58, 0x5A, 0x5B, 0x5D):
                self.ignored['pid_missing_block'] += 1
            elif op not in (0x0D, 0xF5, 0xF8):
                self.ignored[f'opcode_0x{op:02x}'] += 1
            return
        if op == 0x51 and len(packet) >= 18:
            item['gain'] = max(0.0, min(1.0, packet[11] / 100.0))
            duration = struct.unpack_from('<H', packet, 3)[0]
            # PID 0xffff é infinito; zero é uma duração finita de zero ms.
            item['duration_ms'] = None if duration == 0xFFFF else duration
            item['start_delay_ms'] = struct.unpack_from('<H', packet, 9)[0]
            item['direction'] = struct.unpack_from('<H', packet, 14)[0]
        elif op == 0x52 and len(packet) >= 10:
            (item['attack_level'], item['fade_level'], item['attack_time'],
             item['fade_time']) = struct.unpack_from('<HHHH', packet, 2)
        elif op == 0x53 and len(packet) >= 15 and item['kind'] in (9, 10, 11, 12):
            axis = packet[2]
            if axis == 0:
                item['center'], item['coeff_pos'], item['coeff_neg'] = struct.unpack_from('<hhh', packet, 3)
                item['sat_pos'], item['sat_neg'], item['deadband'] = struct.unpack_from('<HHH', packet, 9)
                item['condition_axis'] = 0
                self.pid_counts['condition_updates'] += 1
            else:
                self.ignored['condition_nonsteering_axis'] += 1
        elif op == 0x55 and len(packet) >= 4 and item['kind'] == 1:
            item['level'] = struct.unpack_from('<h', packet, 2)[0]
        elif op == 0x58 and len(packet) >= 4 and item['kind'] == 2:
            # HID ramp logical 0..255; this approximation uses 0..32767.
            item['ramp_start'] = round(packet[2] * 32767 / 255)
            item['ramp_end'] = round(packet[3] * 32767 / 255)
        elif op == 0x5d and len(packet) >= 10 and item['kind'] in (4, 5, 6, 7, 8):
            (item['periodic_magnitude'], item['periodic_offset'],
             item['periodic_phase'], item['period_ms']) = struct.unpack_from('<hhHH', packet, 2)
        elif op == 0x5a and len(packet) >= 4:
            effect_op = packet[2]
            if effect_op in (1, 2):
                if effect_op == 2:
                    for other in self.effects.values():
                        other['running'] = False
                if not item['running']:
                    item['started_at'] = now
                item['running'] = True
                if item['kind'] != 3:
                    self.last_command_time = now
            elif effect_op == 3:
                item['running'] = False
        elif op == 0x5b:
            self.effects.pop(block, None)
        else:
            self.ignored[f'unsupported_report_0x{op:02x}'] += 1
        if item.get('running') and op in (0x51, 0x52, 0x53, 0x55, 0x58, 0x5d):
            self.last_command_time = now

    def _motion(self, position_raw, now):
        pos = max(-32767, min(32767, round((max(0, min(16383, position_raw)) - 8191.5) * 32767 / 8191.5)))
        if self.last_velocity_update is None:
            self.last_velocity_update = now
            self.last_physical_pos = pos
            return pos, 0.0, 0.0
        dt = now - self.last_velocity_update
        if .008 <= dt <= .25:
            v = max(-150000., min(150000., (pos - self.last_physical_pos) / dt))
            alpha = dt / (.045 + dt)
            self.smoothed_velocity += alpha * (v - self.smoothed_velocity)
            accel = max(-2000000., min(2000000.,
                        (self.smoothed_velocity - self.last_inertia_velocity) / dt))
            self.smoothed_acceleration += alpha * (accel - self.smoothed_acceleration)
            self.last_inertia_velocity = self.smoothed_velocity
            self.last_velocity_update, self.last_physical_pos = now, pos
        elif dt > .25:
            self.last_velocity_update, self.last_physical_pos = now, pos
            self.last_inertia_velocity = 0.0
            self.smoothed_velocity = 0.0
            self.smoothed_acceleration = 0.0
        return pos, self.smoothed_velocity, self.smoothed_acceleration

    def _envelope(self, item, elapsed):
        # Approximate HID PID envelope; effect magnitude ramps during attack
        # and fade. Lack of signed envelope fields precludes perfect parity.
        factor = 1.0
        attack_time = item['attack_time'] / 1000.0
        fade_time = item['fade_time'] / 1000.0
        if attack_time > 0 and elapsed < attack_time:
            t = max(0.0, elapsed / attack_time)
            factor *= item['attack_level']/32767.0 * (1-t) + t
        duration = (item['duration_ms'] or 0) / 1000.0
        if duration > 0 and fade_time > 0 and elapsed > max(0.0, duration - fade_time):
            t = min(1.0, (elapsed - (duration - fade_time))/fade_time)
            factor *= (1-t) + item['fade_level']/32767.0*t
        return max(0.0, min(1.0, factor))

    def components(self, position_raw=8192):
        now = self.clock()
        pos, velocity, acceleration = self._motion(position_raw, now)
        result = {key: 0.0 for key in OUTPUT_COMPONENTS}
        # Silêncio do host é legítimo: a vida é definida por Start/Stop,
        # duração e estado PID. O timeout da saída pertence ao backend.
        if not self.armed or self.paused:
            return result
        for item in self.effects.values():
            if not item['running']:
                continue
            kind = item['kind']
            if kind == 3 or kind not in PID_EFFECT_NAMES:
                continue
            elapsed = max(0.0, now - (item['started_at'] if item['started_at'] is not None else now))
            delay = item['start_delay_ms']/1000.0
            if elapsed < delay:
                continue
            elapsed -= delay
            duration_ms = item['duration_ms']
            duration = (duration_ms or 0)/1000.0
            if duration_ms is not None and elapsed >= duration:
                item['running'] = False
                continue
            gain = item['gain'] * self._envelope(item, elapsed)
            force = 0.0
            family = None
            if kind == 1:
                family, force = 'constant', item['level'] * gain
            elif kind == 2:
                # Approximation: direction/ramp sign not encoded in ramp bytes.
                if duration > 0:
                    portion = min(1.0, elapsed/duration)
                else:
                    portion = 0.0
                family = 'ramp'
                force = (item['ramp_start'] * (1-portion) + item['ramp_end'] * portion) * gain
            elif kind in (4, 5, 6, 7, 8) and item['period_ms'] >= 1:
                cycles = elapsed / (item['period_ms'] / 1000.0) + item['periodic_phase']/36000.0
                frac = cycles % 1.0
                if kind == 4:
                    waveform = 1.0 if frac < .5 else -1.0
                elif kind == 5:
                    waveform = math.sin(2*math.pi*frac)
                elif kind == 6:
                    waveform = (2/math.pi)*math.asin(math.sin(2*math.pi*frac))
                elif kind == 7:
                    waveform = 2*frac - 1
                else:
                    waveform = 1 - 2*frac
                family = 'periodic'
                force = (item['periodic_offset'] + item['periodic_magnitude'] * waveform) * gain
            elif kind in (9, 10, 11, 12) and item['condition_axis'] == 0:
                if kind == 9:
                    delta = pos - item['center']
                    if abs(delta) <= item['deadband']: continue
                    coeff = abs(item['coeff_pos'] if delta > 0 else item['coeff_neg'])
                    sat = item['sat_pos'] if delta > 0 else item['sat_neg']
                    mag = min(32767, sat, round((abs(delta) - item['deadband']) * coeff / 32767))
                    family = 'spring'
                    force = self.spring_direction * (1 if delta > 0 else -1) * mag * gain
                elif kind == 10:
                    if abs(velocity) < 300: continue
                    coeff = abs(item['coeff_pos'] if velocity > 0 else item['coeff_neg'])
                    sat = item['sat_pos'] if velocity > 0 else item['sat_neg']
                    mag = min(32767, sat, round(abs(velocity)*coeff/65534))
                    family, force = 'damper', -(1 if velocity > 0 else -1) * mag * gain
                elif kind == 11:
                    if abs(acceleration) < 6000: continue
                    coeff = abs(item['coeff_pos'] if acceleration > 0 else item['coeff_neg'])
                    sat = item['sat_pos'] if acceleration > 0 else item['sat_neg']
                    mag = min(32767, sat, round(abs(acceleration)*coeff/350000))
                    family, force = 'inertia', -(1 if acceleration > 0 else -1)*mag*gain
                else:
                    if abs(velocity) < 200: continue
                    coeff = abs(item['coeff_pos'] if velocity > 0 else item['coeff_neg'])
                    sat = item['sat_pos'] if velocity > 0 else item['sat_neg']
                    mag = min(32767, sat, round(coeff * min(1.,abs(velocity)/8000.)))
                    family, force = 'friction', -(1 if velocity > 0 else -1)*mag*gain
            if family is not None:
                result[family] += force
                if force:
                    self.rendered_by_kind[PID_EFFECT_NAMES[kind]] += 1
        for family in result:
            result[family] = max(-32767, min(32767, round(result[family]*self.gain)))
            self.component_peaks[family] = max(self.component_peaks[family],abs(result[family]))
            if result[family]: self.component_ticks[family] += 1
        if result['spring']:
            self.spring_active_ticks += 1
            self.spring_peak = max(self.spring_peak,abs(result['spring']))
        if result['damper']: self.damper_active_ticks += 1
        if result['periodic']: self.periodic_active_ticks += 1
        return result

    def raw_level(self, position_raw=8192):
        return max(-32767,min(32767,sum(self.components(position_raw).values())))
