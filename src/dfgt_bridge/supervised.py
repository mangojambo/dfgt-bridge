# SPDX-License-Identifier: GPL-2.0-only
"""Política supervisionada determinística, sem abrir dispositivos.

O processo de I/O injeta EvdevBackend; testes/replay injetam MockBackend.
Somente step(frame) válido renova FF. poll nunca renova força.
"""
from dataclasses import asdict
import math

from .ffb.effects import OUTPUT_COMPONENTS
from .ffb.safety import ConstantOutput


class SupervisedPolicy:
    def __init__(self, backend, *, strength=10, maximum=20, polarity=1, session_s=120):
        if not (type(strength) is int and 0 <= strength <= 10 and
                type(maximum) is int and 10 <= maximum <= 30 and polarity in (-1, 1)):
            raise ValueError('Inicial 0..10%, máximo 10..30%, polaridade ±1')
        self.now_ns = 0
        self.output = ConstantOutput(backend, strength, clock=lambda: self.now_ns/1e9,
                                     max_strength_percent=maximum, test_limit_s=session_s,
                                     slew_per_s=16383.5)
        self.polarity = polarity
        self.last_generation = 0
        self.last_valid_ns = None
        self.latch_ns = None
        self.arm_after_ns = 0
        self.minimum_epoch = 0
        self.latched = False
        self.hard_fault = False
        self.reason = 'awaiting_first_calculation'
        self.events = self.accepted = self.rejected = self.deduplicated = 0
        self.latency_ns = self.max_latency_ns = 0
        self.frame = None
        self.closed = False

    def stop(self, reason, *, latch=False, fatal=False):
        self.output.stop()
        self.output.last_tick = -1e9
        if not self.latched or latch:
            self.reason = reason
        if latch:
            self.latched = True
            self.latch_ns = self.now_ns
            self.output.set_enabled(False)
            self.events += 1
        self.hard_fault |= fatal

    def check(self):
        if self.last_valid_ns is not None and self.now_ns-self.last_valid_ns >= 100_000_000 and not self.latched:
            self.stop('watchdog_calculation_stale', latch=True)
        if self.output.first_active is not None and self.now_ns/1e9-self.output.first_active >= self.output.test_limit_s:
            self.hard_fault = True
            if not self.latched:
                self.stop('supervised_session_deadline', latch=True, fatal=True)

    def apply(self, action, data, now_ns):
        if type(now_ns) is not int or now_ns < self.now_ns:
            raise ValueError('Relógio supervisionado regressivo')
        self.now_ns = now_ns
        if self.closed:
            raise ValueError('Política encerrada')
        try:
            self.check()
            if action == 'frame':
                generation, calculated = data['generation'], data['calculated_ns']
                components = data['components']
                if (type(generation) is not int or generation <= self.last_generation or
                        type(calculated) is not int or calculated > now_ns or now_ns-calculated >= 100_000_000 or
                        set(components) != set(OUTPUT_COMPONENTS) or
                        not all(math.isfinite(v) for v in components.values())):
                    self.rejected += 1
                    self.stop('invalid_or_stale_calculation', latch=True)
                elif data.get('stop_epoch', 0) < self.minimum_epoch:
                    self.rejected += 1
                    self.reason = 'discarded_pre_stop_frame'
                else:
                    self.last_generation = generation
                    if calculated < self.arm_after_ns:
                        self.rejected += 1
                        self.reason = 'awaiting_post_arm_calculation'
                    elif self.last_valid_ns is not None and calculated <= self.last_valid_ns:
                        self.rejected += 1
                        self.stop('calculation_clock_not_advancing', latch=True)
                    else:
                        self.frame = data
                        self.last_valid_ns = calculated
                        self.accepted += 1
                        self.latency_ns = now_ns-calculated
                        self.max_latency_ns = max(self.max_latency_ns, self.latency_ns)
                        if not self.latched:
                            before = self.output.writes
                            self.output.tick({k: v*self.polarity for k, v in components.items()})
                            self.deduplicated += int(self.output.playing and self.output.writes == before)
                            self.reason = (data.get('zero_reason') or 'renderer_zero') if self.output.last_sent == 0 else None
            elif action == 'stop':
                self.minimum_epoch = max(self.minimum_epoch, data.get('epoch', 0))
                self.stop(data['reason'], latch=data.get('latch', False), fatal=data.get('fatal', False))
            elif action == 'strength':
                self.output.set_strength(data['percent'])
                if self.output.strength_percent == 0:
                    self.reason = 'strength_zero'
            elif action == 'arm':
                if not self.hard_fault and (self.latch_ns is None or data['requested_ns'] > self.latch_ns):
                    self.latched = False
                    self.last_valid_ns = None
                    self.arm_after_ns = now_ns
                    self.output.set_enabled(True)
                    self.reason = 'explicit_arm_awaiting_fresh_calculation'
            elif action == 'poll':
                pass
            elif action == 'close':
                self.stop('runner_shutdown')
                self.output.close()
                self.closed = True
            else:
                raise ValueError('Ação física desconhecida')
        except BaseException:
            self.hard_fault = self.latched = True
            self.reason = 'output_exception'
            try:
                self.output.close()
            finally:
                self.closed = True
            raise
        return self.snapshot()

    def snapshot(self):
        mix = self.output.last_mix
        return dict(strength=self.output.strength_percent, maximum=self.output.max_strength_percent,
                    polarity=self.polarity, latched=self.latched, hard_fault=self.hard_fault,
                    closed=self.closed, zero_reason=self.reason,
                    renderer_components=self.frame['components'] if self.frame else {},
                    game_force=self.frame.get('game_force', {}) if self.frame else {},
                    mix=asdict(mix) if mix else None, evdev_level=self.output.last_sent,
                    uploads=self.output.writes, deduplicated=self.deduplicated,
                    plays=self.output.play_requests, renewals=self.output.renewals,
                    accepted=self.accepted, rejected=self.rejected, safety_events=self.events,
                    latency_ns=self.latency_ns, max_latency_ns=self.max_latency_ns,
                    age_ns=None if self.last_valid_ns is None else self.now_ns-self.last_valid_ns,
                    last_generation=self.last_generation, clipped=self.output.saturation_ticks)
