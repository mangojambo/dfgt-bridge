# SPDX-License-Identifier: GPL-2.0-only
# Extraído da v0.13 preservada em reference/v0.13/.
"""Estado/decodificação PID; matemática pura em renderer e soma em mixer."""
from collections import Counter
import math
import struct
import time

from .renderer import (ANGLE_MAX, MotionEstimator, condition_force,
                       effect_projection, envelope_magnitude,
                       enveloped_constant, periodic_wave, ramp_level)


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
    """Converte o estado PID em componentes de força de um único eixo.

    Use components() para alimentar o backend sem clipping intermediário.
    Limitações de ciclo de vida permanecem explícitas na suite de auditoria.
    """
    def __init__(self, clock=time.monotonic, *, velocity_reference=2.0,
                 acceleration_reference=4.0):
        super().__init__()
        if not all(math.isfinite(v) and v > 0 for v in (
                velocity_reference, acceleration_reference)):
            raise ValueError('Escalas de movimento inválidas')
        self.velocity_reference = velocity_reference
        self.acceleration_reference = acceleration_reference
        self.motion_estimator = MotionEstimator()
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
        self.spring_peak = 0
        self.spring_active_ticks = 0
        self.periodic_active_ticks = 0
        self.damper_active_ticks = 0
        self.component_peaks = Counter()
        self.component_ticks = Counter()

    def arm(self):
        # Discard attach-time kernel probes; game effects start after this.
        self.effects.clear()
        self.gain = 1.0
        self.paused = False
        self.last_command_time = -1e9
        self.armed = True
        self.motion_estimator.reset()

    def _new_effect(self, kind):
        return dict(kind=kind, level=0, running=False, gain=1.0,
                    center=0, coeff_pos=0, coeff_neg=0,
                    sat_pos=0, sat_neg=0, deadband=0,
                    condition_axis=-1, periodic_magnitude=0,
                    periodic_offset=0, periodic_phase=0, period_ms=0,
                    ramp_start=0, ramp_end=0, duration_ms=None,
                    start_delay_ms=0, direction=9000,
                    axes_enabled=1, direction_enabled=False,
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
        if not math.isfinite(now):
            raise ValueError('Relógio PID deve ser finito')
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
            direction = struct.unpack_from('<H', packet, 14)[0]
            if direction > ANGLE_MAX:
                self.ignored['invalid_direction'] += 1
                return
            item['gain'] = max(0.0, min(1.0, packet[11] / 100.0))
            duration = struct.unpack_from('<H', packet, 3)[0]
            # PID 0xffff é infinito; zero é uma duração finita de zero ms.
            item['duration_ms'] = None if duration == 0xFFFF else duration
            item['start_delay_ms'] = struct.unpack_from('<H', packet, 9)[0]
            item['direction'] = direction
            item['axes_enabled'] = packet[13] & 3
            item['direction_enabled'] = bool(packet[13] & 4)
        elif op == 0x52 and len(packet) >= 10:
            values = struct.unpack_from('<HHHH', packet, 2)
            if any(value > 32767 for value in values):
                self.ignored['invalid_envelope'] += 1
                return
            (item['attack_level'], item['fade_level'], item['attack_time'],
             item['fade_time']) = values
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
            values = struct.unpack_from('<HhHH', packet, 2)
            if values[0] > 32767 or values[2] > ANGLE_MAX or values[3] > 32767:
                self.ignored['invalid_periodic'] += 1
                return
            (item['periodic_magnitude'], item['periodic_offset'],
             item['periodic_phase'], item['period_ms']) = values
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

    def components(self, position_raw=8192, *, motion=None):
        now = self.clock()
        if not math.isfinite(now):
            raise ValueError('Relógio PID deve ser finito')
        motion = self.motion_estimator.sample(position_raw, now) if motion is None else motion
        result = {key: 0.0 for key in OUTPUT_COMPONENTS}
        contributions = {key: [] for key in OUTPUT_COMPONENTS}
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
            gain = item['gain']
            force = 0.0
            family = None
            if kind == 1:
                family, force = 'constant', enveloped_constant(item['level'], item, elapsed) * gain
            elif kind == 2:
                family = 'ramp'
                force = ramp_level(item, elapsed) * gain
            elif kind in (4, 5, 6, 7, 8) and item['period_ms'] >= 1:
                period = item['period_ms'] / 1000.0
                frac = ((elapsed % period) / period + item['periodic_phase']/36000.0) % 1.0
                waveform = periodic_wave(kind, frac)
                family = 'periodic'
                magnitude = envelope_magnitude(item['periodic_magnitude'], item, elapsed)
                force = (item['periodic_offset'] + magnitude * waveform) * gain
            elif kind in (9, 10, 11, 12) and item['condition_axis'] == 0:
                family = PID_EFFECT_NAMES[kind]
                force = condition_force(item, motion,
                                        velocity_reference=self.velocity_reference,
                                        acceleration_reference=self.acceleration_reference) * item['gain']
            if family is not None:
                if kind not in (9, 10, 11, 12):
                    force *= effect_projection(item)
                contributions[family].append(force)
                if force:
                    self.rendered_by_kind[PID_EFFECT_NAMES[kind]] += 1
        for family in result:
            # Preserve cancelamentos e precisão; o teto fica na soma final.
            result[family] = math.fsum(contributions[family]) * self.gain
            self.component_peaks[family] = max(self.component_peaks[family],abs(result[family]))
            if result[family]: self.component_ticks[family] += 1
        if result['spring']:
            self.spring_active_ticks += 1
            self.spring_peak = max(self.spring_peak,abs(result['spring']))
        if result['damper']: self.damper_active_ticks += 1
        if result['periodic']: self.periodic_active_ticks += 1
        return result

    def raw_level(self, position_raw=8192):
        """Helper legado já quantizado/limitado; backend deve usar components()."""
        return max(-32767, min(32767, round(math.fsum(self.components(position_raw).values()))))
