# SPDX-License-Identifier: GPL-2.0-only
"""Sessão passiva em memória: uma implementação para live e replay."""
from collections import Counter
from dataclasses import asdict
import hashlib
import math
import struct

from .capture import digest, implementation_hash
from .ffb.renderer import MotionEstimator, MotionState
from .ffb.session import OfflineSession
from .input.virtual_g29 import descriptor, input_event, report, VENDOR, PRODUCT


def describe_uhid(raw):
    result = {'origin': 'UHID', 'event_type': None, 'report_type': None,
              'report_id': None, 'request_id': None, 'packet_hex': None}
    if len(raw) < 4:
        return result
    kind = struct.unpack_from('=I', raw)[0]
    result['event_type'] = kind
    if kind == 6 and len(raw) >= 4103:
        size, rtype = struct.unpack_from('=HB', raw, 4100)
        packet = raw[4:4+min(size, 4096)]
        result.update(report_type=rtype, report_id=packet[0] if packet else None,
                      packet_hex=packet.hex(), declared_size=size)
    elif kind in (9, 13):
        if len(raw) >= 8:
            result['request_id'] = struct.unpack_from('=I', raw, 4)[0]
        if len(raw) >= 10:
            result.update(report_id=raw[8], report_type=raw[9])
        if kind == 13 and len(raw) >= 12:
            size = struct.unpack_from('=H', raw, 10)[0]
            result.update(packet_hex=raw[12:12+min(size, 4096)].hex(), declared_size=size)
    return result


class PassiveSession:
    """Sem fd e sem escolha de backend: a saída é sempre MockBackend.

    timestamp_ns é chegada monotônica. sample_ns é o timestamp monotônico
    do SYN_REPORT, quando disponível; derivadas não usam o ritmo de FFB.
    """
    def __init__(self, origin_ns, *, capacity=40, strength_percent=20,
                 gas_axis='z', invert_gas=False):
        if gas_axis not in ('z', 'y', 'both') or type(invert_gas) is not bool:
            raise ValueError('Opções de entrada inválidas')
        self.origin_ns = self.timestamp_ns = origin_ns
        self.config = dict(capacity=capacity, strength_percent=strength_percent,
                           gas_axis=gas_axis, invert_gas=invert_gas)
        self.core = OfflineSession(self.clock, capacity=capacity, strength_percent=strength_percent,
                                   close_terminates=False, test_limit_s=None)
        self.estimator = MotionEstimator()
        self.motion = MotionState((8192-8191.5)/8191.5)
        self.sample_ns = None
        self.last_state_report = None
        self.last_input = report((8192, 255, 255, 0, 0, []), gas_axis, invert_gas)
        self.core.transport.input_report = lambda: self.last_input
        self.phase = 'initialization_unattributed'
        self.previous_state = self.state()
        self.counts = Counter()
        self.types = Counter()
        self.phase_counts = {phase: Counter() for phase in (
            'initialization_unattributed', 'game_operator_marked', 'menu_operator_marked')}
        self.last_result = None

    def clock(self):
        return (self.timestamp_ns-self.origin_ns)/1e9

    def metadata(self):
        return dict(origin_ns=self.origin_ns, config=self.config,
                    descriptor_hex=descriptor().hex(), descriptor_sha256=hashlib.sha256(descriptor()).hexdigest(),
                    virtual_id=[VENDOR, PRODUCT], backend='mock',
                    producer_attribution='unknown; phase markers are operator annotations',
                    semantics='passive-v1', implementation_sha256=implementation_hash(),
                    byteorder=__import__('sys').byteorder)

    def state(self):
        engine = self.core.engine
        return dict(effects={str(k): dict(v) for k, v in sorted(engine.effects.items())},
                    paused=engine.paused, pause_started=engine.pause_started,
                    paused_duration=engine.paused_duration, gain=engine.gain,
                    enabled=engine.actuators_enabled, armed=engine.armed,
                    pool=asdict(engine.pool.last), closed=self.core.closed)

    def apply(self, kind, data, timestamp_ns):
        if type(timestamp_ns) is not int or timestamp_ns < self.timestamp_ns:
            raise ValueError('Timestamp monotônico inválido ou regressivo')
        if self.core.closed and kind != 'close':
            raise ValueError('Sessão encerrada')
        self.timestamp_ns = timestamp_ns
        self.counts[kind] += 1
        writes, info = [], {}
        engine, output = self.core.engine, self.core.output
        previous_counts = dict(self.core.transport.counts)
        previous_ignored = dict(engine.ignored)
        actions_before = output.dev.dropped_actions + len(output.dev.actions)
        output_before = (output.writes, output.play_requests, output.last_sent)
        if self.sample_ns is not None and timestamp_ns-self.sample_ns > 250_000_000:
            self.motion = MotionState(self.motion.position)
        self.core.motion = self.motion
        if kind == 'input':
            state = data['state']
            if len(state) != 6 or not all(type(v) is int for v in state[:5]):
                raise ValueError('Estado de entrada inválido')
            if not (0 <= state[0] <= 16383 and all(0 <= v <= 255 for v in state[1:3])
                    and all(-1 <= v <= 1 for v in state[3:5])):
                raise ValueError('Entrada fora dos ranges da v0.8')
            sample = data.get('sample_ns', timestamp_ns)
            if type(sample) is not int or sample > timestamp_ns:
                raise ValueError('Timestamp de amostra inválido')
            # Um lote anterior ao snapshot inicial não inventa derivadas.
            if self.sample_ns is None or sample >= self.sample_ns:
                self.motion = self.estimator.sample(state[0], (sample-self.origin_ns)/1e9)
                self.sample_ns = sample
            else:
                self.motion = MotionState((state[0]-8191.5)/8191.5)
                info['motion_reason'] = 'sample_older_than_initial_snapshot'
            self.last_input = report(state, self.config['gas_axis'], self.config['invert_gas'])
            writes.append(input_event(self.last_input))
            self.core.tick(state[0], motion=self.motion)
            info['input_report_hex'] = self.last_input.hex()
        elif kind == 'uhid':
            raw = bytes.fromhex(data['raw_hex'])
            info.update(describe_uhid(raw))
            writes.extend(self.core.handle(raw))
            applied = self.core.transport.counts['writes'] > previous_counts.get('writes', 0)
            packet = bytes.fromhex(info['packet_hex'] or '')
            info['block'] = (packet[1] if info['report_type'] == 1 and len(packet) >= 2
                             and packet[0] in (0x51, 0x52, 0x53, 0x55, 0x58, 0x5a, 0x5b, 0x5d)
                             else engine.last_block if info['report_id'] in (0x54, 0x56)
                             and info['report_type'] == 0 else None)
            if info['report_id'] == 0x54 and info['report_type'] == 0 and applied:
                if engine.pool.last.status == 1 and not self.core.transport.last_error:
                    self.types[str(engine.effects[engine.last_block]['kind'])] += 1
                elif engine.pool.last.status != 1:
                    self.counts['allocation_failures'] += 1
            event_kind = str(info['event_type']) if info['event_type'] in (2, 3, 4, 5, 6, 9, 13) else 'other'
            self.counts['uhid_' + event_kind] += 1
        elif kind == 'tick':
            self.core.tick(motion=self.motion)
        elif kind == 'mark':
            if data.get('phase') not in ('initialization_unattributed', 'game_operator_marked', 'menu_operator_marked'):
                raise ValueError('Marcador de fase inválido')
            self.phase = data['phase']
        elif kind == 'strength':
            if type(data.get('percent')) is not int or not 0 <= data['percent'] <= 60:
                raise ValueError('Intensidade mock deve estar em 0..60%')
            output.set_strength(data['percent'])
            self.core.tick(motion=self.motion)
        elif kind == 'close':
            self.core.close()
            self.core.last_components = {}
        else:
            raise ValueError('Ação de sessão desconhecida')
        # PID State Input espontâneo: somente transições, depois de ACKs.
        state_report = engine.state_report()
        if kind != 'close' and not self.core.closed and state_report != self.last_state_report:
            writes.append(input_event(state_report))
        self.last_state_report = state_report
        current = self.state()
        changes = {key: value for key, value in current.items() if value != self.previous_state.get(key)}
        old_effects = self.previous_state['effects']
        new_effects = current['effects']
        created = sorted(set(new_effects)-set(old_effects), key=int)
        freed = sorted(set(old_effects)-set(new_effects), key=int)
        self.counts['created'] += len(created)
        self.counts['freed'] += len(freed)
        self.previous_state = current
        delta = {key: self.core.transport.counts[key]-previous_counts.get(key, 0)
                 for key in self.core.transport.counts
                 if self.core.transport.counts[key] != previous_counts.get(key, 0)}
        ignored = {key: engine.ignored[key]-previous_ignored.get(key, 0)
                   for key in engine.ignored if engine.ignored[key] != previous_ignored.get(key, 0)}
        error = self.core.transport.last_error if kind == 'uhid' else None
        replies = [w.hex() for w in writes if struct.unpack_from('=I', w)[0] in (10, 14)]
        if not error and any(struct.unpack_from('=H', bytes.fromhex(r), 8)[0] for r in replies):
            error = 'cached_request_error'
        mix = asdict(output.last_mix) if output.last_mix and not self.core.closed else None
        components = dict(self.core.last_components)
        if self.core.closed:
            components = {}
        actions_count = output.dev.dropped_actions + len(output.dev.actions)-actions_before
        actions = []
        for entry in output.dev.actions[-actions_count:] if actions_count else ():
            if entry[0] == 'upload':
                actions.append(['upload', asdict(entry[1])])
            elif entry[0] == 'write':
                actions.append(['write', list(entry[1])])
            else:
                actions.append(list(entry))
        result = dict(info=info, phase=self.phase, producer='unattributed',
                      error=error, transport_delta=delta, renderer_ignored=ignored, created=created, freed=freed,
                      transitions=changes, state_sha256=digest(current), pid_state_hex=state_report.hex(),
                      replies_hex=replies, writes_hex=[w.hex() for w in writes],
                      components=components, renderer_total=math.fsum(components.values()),
                      mix=mix, final_force=output.dev.level, last_sent=output.last_sent,
                      uploads=output.writes-output_before[0], plays=output.play_requests-output_before[1],
                      mock_actions=actions,
                      mock=dict(effect=asdict(output.dev.effect) if output.dev.effect else None,
                                active_until=output.dev.active_until, playing=output.dev.playing,
                                output_enabled=output.enabled, output_closed=output.closed),
                      motion=asdict(self.motion),
                      reason=('rejected:' + error if error else 'ignored:' + ','.join(sorted(ignored)) if ignored
                              else 'duplicate_request' if delta.get('duplicates')
                              else 'clipping' if mix and mix['clipped'] else
                              'rate_gate_or_slew' if mix and round(mix['limited']) != output.last_sent
                              and not self.core.closed else None))
        self.counts['invalid'] += int(error is not None)
        if error:
            self.counts['rejected_' + error] += 1
        self.counts['clipping'] += int(bool(mix and mix['clipped']))
        self.counts['processing_actions'] += 1
        self.phase_counts[self.phase][kind] += 1
        if kind == 'uhid':
            self.phase_counts[self.phase]['uhid_' + event_kind] += 1
        self.last_result = result
        return result

    def summary(self):
        effects = self.core.engine.effects
        running = sum(bool(v['running']) for v in effects.values())
        return dict(counts=dict(self.counts), types=dict(self.types),
                    phases={key: dict(value) for key, value in self.phase_counts.items()}, allocated=len(effects),
                    active=0 if self.core.engine.paused else running,
                    paused=running if self.core.engine.paused else 0,
                    uploads=self.core.output.writes, plays=self.core.output.play_requests,
                    renewals=self.core.output.renewals, peak=self.core.output.peak,
                    component_peaks=dict(self.core.output.component_stats),
                    mock_history_dropped=self.core.output.dev.dropped_actions,
                    transport=dict(self.core.transport.counts),
                    duration_s=self.clock(), phase=self.phase)
