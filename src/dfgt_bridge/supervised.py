# SPDX-License-Identifier: GPL-2.0-only
"""Política supervisionada determinística, sem abrir dispositivos.

O processo de I/O injeta EvdevBackend; testes/replay injetam MockBackend.
Somente step(frame) válido renova FF. poll nunca renova força.
"""
from dataclasses import asdict
from collections import deque
import math

from .ffb.effects import OUTPUT_COMPONENTS
from .ffb.safety import ConstantOutput, validate_percent


class SupervisedPolicy:
    def __init__(self, backend, *, strength=10, maximum=20, polarity=1, session_s=120,
                 require_game=False, slew_per_s=16383.5):
        validate_percent(strength)
        validate_percent(maximum)
        if strength > maximum or type(polarity) is not int or polarity not in (-1, 1):
            raise ValueError('Intensidade inicial 0..máximo, teto 0..100%, polaridade ±1')
        if (type(slew_per_s) not in (int, float) or not math.isfinite(slew_per_s)
                or slew_per_s <= 0):
            raise ValueError('Slew físico deve ser finito e positivo em unidades/s')
        self.now_ns = 0
        self.output = ConstantOutput(backend, strength, clock=lambda: self.now_ns/1e9,
                                     max_strength_percent=maximum, test_limit_s=session_s,
                                     slew_per_s=slew_per_s)
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
        self.rate_limited = 0
        self.latency_ns = self.max_latency_ns = 0
        self.frame = None
        self.closed = False
        self.require_game = require_game
        self.game_running = not require_game
        self.game_seen = False
        self.output_stage = 'awaiting_frame'
        self.measurement_start_ns = None
        self.upload_times = deque(maxlen=256)
        self.play_times = deque(maxlen=256)
        self.observed_uploads = self.observed_plays = 0

    def stop(self, reason, *, latch=False, fatal=False):
        self.output.stop()
        self.output_stage = 'safety_stop'
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
            if self.measurement_start_ns is None:
                self.measurement_start_ns = now_ns
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
                        if not self.latched and self.game_running:
                            before = self.output.writes
                            previous_tick = self.output.last_tick
                            self.output.tick({k: v*self.polarity for k, v in components.items()})
                            target = round(self.output.last_mix.limited) if self.output.last_mix else 0
                            self.output_stage = ('zero_request' if target == 0 else
                                'rate_gate' if self.now_ns/1e9-previous_tick < self.output.interval else
                                'slew' if self.output.last_sent != target else 'direct')
                            if self.output.playing and self.output.writes == before:
                                if self.now_ns/1e9-previous_tick >= self.output.interval:
                                    self.deduplicated += 1
                                else:
                                    self.rate_limited += 1
                            if self.output.last_sent != 0:
                                self.reason = None
                            elif self.output.strength_percent == 0:
                                self.reason = 'strength_zero'
                            else:
                                self.reason = data.get('zero_reason') or ('renderer_zero' if
                                    math.fsum(components.values()) == 0 else 'quantized_or_rate_limited_zero')
                        elif not self.game_running and not self.latched:
                            self.reason = 'waiting_for_DiRT5_process'
            elif action == 'stop':
                self.minimum_epoch = max(self.minimum_epoch, data.get('epoch', 0))
                self.stop(data['reason'], latch=data.get('latch', False), fatal=data.get('fatal', False))
            elif action == 'strength':
                self.output.set_strength(validate_percent(data['percent']))
                self.output_stage = 'strength_change_awaiting_frame'
                if self.output.strength_percent == 0:
                    self.reason = 'strength_zero'
            elif action == 'arm':
                if (not self.hard_fault and self.game_running and
                        (self.latch_ns is None or data['requested_ns'] > self.latch_ns)):
                    self.latched = False
                    self.last_valid_ns = None
                    self.arm_after_ns = now_ns
                    self.output.set_enabled(True)
                    self.reason = 'explicit_arm_awaiting_fresh_calculation'
            elif action == 'poll':
                pass
            elif action == 'game':
                self.game_running = bool(data['running'])
                if self.game_running:
                    self.game_seen = True
                else:
                    self.stop('game_exit' if self.game_seen else 'waiting_for_DiRT5_process', latch=self.game_seen)
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
        self.upload_times.extend([now_ns]*(self.output.writes-self.observed_uploads))
        self.play_times.extend([now_ns]*(self.output.play_requests-self.observed_plays))
        self.observed_uploads, self.observed_plays = self.output.writes, self.output.play_requests
        return self.snapshot()

    def force_pipeline(self, raw_total):
        """Grandezas comandadas; não é medição do torque nem leitura do ganho global."""
        gain = self.output.cap/32767
        after_gain = raw_total*self.polarity*gain if raw_total is not None else None
        limited = max(-self.output.cap, min(self.output.cap, after_gain)) if after_gain is not None else None
        sent = self.output.last_sent

        def loss(before, after):
            if before in (None, 0):
                return None  # Sem referência; não inventar percentuais para cancelamento/zero.
            return max(0.0, min(100.0, 100*(1-abs(after)/abs(before))))

        tracking_loss = loss(limited, sent)
        window = min(1.0, (self.now_ns-self.measurement_start_ns)/1e9) if self.measurement_start_ns is not None else 0
        uploads = sum(self.now_ns-t < 1_000_000_000 for t in self.upload_times)
        plays = sum(self.now_ns-t < 1_000_000_000 for t in self.play_times)
        tracking_error = None if limited in (None, 0) else abs(1-sent/limited)*100
        if tracking_error is not None and not math.isfinite(tracking_error):
            tracking_error = None
        return dict(renderer_signed_reference=None if raw_total is None else max(-32768, min(32767, raw_total)),
                    user_strength_percent=self.output.strength_percent,
                    maximum_percent=self.output.max_strength_percent, user_gain=gain,
                    evdev_gain_expected=65535 if self.output.playing else 0,
                    after_user_gain=after_gain, after_clipping=limited,
                    after_safety_slew=sent, evdev_magnitude=abs(sent),
                    slew_per_s=self.output.slew_per_s, stage=self.output_stage,
                    direction=0x4000,
                    uploads_last_second=uploads, uploads_per_second=uploads/window if window else 0,
                    plays_per_second=plays/window if window else 0,
                    loss_percent=dict(user_gain=100*(1-gain), clipping=loss(after_gain, limited),
                        slew=tracking_loss if self.output_stage == 'slew' else 0,
                        rate_gate=tracking_loss if self.output_stage == 'rate_gate' else 0,
                        safety=tracking_loss if self.output_stage == 'safety_stop' else 0,
                        total=loss(raw_total, sent)),
                    tracking_error_percent=tracking_error,
                    sign_mismatch=limited is not None and limited*sent < 0)

    def snapshot(self):
        mix = self.output.last_mix
        try:
            raw_total = math.fsum(self.frame['components'].values()) if self.frame else 0
        except OverflowError:
            raw_total = None
        return dict(strength=self.output.strength_percent, maximum=self.output.max_strength_percent,
                    polarity=self.polarity, latched=self.latched, hard_fault=self.hard_fault,
                    closed=self.closed, zero_reason=self.reason,
                    game_running=self.game_running,
                    renderer_components=self.frame['components'] if self.frame else {},
                    renderer_total=raw_total, requested_before_limit=mix.requested if mix else 0,
                    force_pipeline=self.force_pipeline(raw_total),
                    limited_force=mix.limited if mix else 0,
                    game_force=self.frame.get('game_force', {}) if self.frame else {},
                    effects=self.frame.get('effects', []) if self.frame else [],
                    mix=asdict(mix) if mix else None, evdev_level=self.output.last_sent,
                    uploads=self.output.writes, deduplicated=self.deduplicated,
                    rate_limited=self.rate_limited,
                    plays=self.output.play_requests, renewals=self.output.renewals,
                    accepted=self.accepted, rejected=self.rejected, safety_events=self.events,
                    latency_ns=self.latency_ns, max_latency_ns=self.max_latency_ns,
                    age_ns=None if self.last_valid_ns is None else self.now_ns-self.last_valid_ns,
                    last_generation=self.last_generation, clipped=self.output.saturation_ticks)
