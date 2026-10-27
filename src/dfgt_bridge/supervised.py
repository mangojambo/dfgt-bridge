# SPDX-License-Identifier: GPL-2.0-only
"""Política supervisionada determinística, sem abrir dispositivos.

O processo de I/O injeta EvdevBackend; testes/replay injetam MockBackend.
Somente step(frame) válido renova FF. poll nunca renova força.
"""
from dataclasses import asdict
from collections import deque
from copy import deepcopy
import math

from .ffb.effects import OUTPUT_COMPONENTS
from .ffb.safety import ConstantOutput, validate_percent
from .ffb.mixer import calibrate_components, validate_effect_gains
from .ffb.command_stats import CommandStatistics
from .config import FFB_DEFAULTS


class SupervisedPolicy:
    def __init__(self, backend, *, strength=FFB_DEFAULTS['ffb_strength'],
                 maximum=FFB_DEFAULTS['ffb_max_strength'], polarity=FFB_DEFAULTS['ffb_polarity'],
                 session_s=FFB_DEFAULTS['ffb_seconds'], require_game=False,
                 slew_per_s=FFB_DEFAULTS['ffb_slew_rate'], effect_gains=None,
                 legacy_spring_keys=False):
        validate_percent(strength)
        validate_percent(maximum)
        if strength > maximum or type(polarity) is not int or polarity not in (-1, 1):
            raise ValueError('Intensidade inicial 0..máximo, teto 0..100%, polaridade ±1')
        if (type(slew_per_s) not in (int, float) or not math.isfinite(slew_per_s)
                or slew_per_s <= 0):
            raise ValueError('Slew físico deve ser finito e positivo em unidades/s')
        self.now_ns = 0
        self.effect_gains = validate_effect_gains(
            {'spring': FFB_DEFAULTS['ffb_spring_gain']} if effect_gains is None else effect_gains)
        self.spring_profile_gain = self.effect_gains.get('spring', 1.0)
        self.spring_selected = self.spring_applied = self.spring_from = self.spring_profile_gain
        self.spring_started_ns = None
        self.spring_command_gain = None
        self.last_calibration_event = None
        # Replay-only compatibility for traces recorded before the ×3/×4 trial.
        self.spring_targets = {'F5': 2.0, 'F6': 3.0} if legacy_spring_keys else {'F5': 3.0, 'F6': 4.0}
        self.command_stats = CommandStatistics(gains=(2, 3) if legacy_spring_keys else (2, 3, 4))
        self.calibrated_components = dict.fromkeys(OUTPUT_COMPONENTS, 0)
        self.calibrated_total = 0
        self.output = ConstantOutput(backend, strength, clock=lambda: self.now_ns/1e9,
                                     max_strength_percent=maximum, test_limit_s=session_s,
                                     slew_per_s=slew_per_s, allow_unlimited=True)
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
        self.first_latch = self.last_latch = None
        self.last_rejection = None
        self.rejected_by_reason = {}
        self.last_stop_reason = None
        self.last_pid_command = None
        self.stop_epoch = 0
        self.game_identity = self.previous_game_identity = None
        self.action = None
        self.incoming_frame = None
        self.idle_wait = False
        self.resume_after_ns = 0
        self.idle_timeouts = self.idle_recoveries = 0
        self.last_idle_timeout = None

    def spring_gain_at(self, now_ns):
        if self.spring_started_ns is None:
            return self.spring_selected
        if now_ns-self.spring_started_ns >= 300_000_000:
            return self.spring_selected  # Exact endpoint, including custom starting gains.
        fraction = min(1.0, max(0.0, (now_ns-self.spring_started_ns)/300_000_000))
        return self.spring_from+(self.spring_selected-self.spring_from)*fraction

    def spring_diagnostic(self):
        interpolated = self.spring_gain_at(self.now_ns)
        progress = (1.0 if self.spring_started_ns is None else
                    min(1.0, max(0.0, (self.now_ns-self.spring_started_ns)/300_000_000)))
        return dict(profile=self.spring_profile_gain, selected=self.spring_selected,
                    applied=self.spring_applied, interpolated=interpolated,
                    command_gain=self.spring_command_gain, transition_progress=progress,
                    transition_active=interpolated != self.spring_selected,
                    duration_ms=300, started_ns=self.spring_started_ns,
                    last_calculation_ns=self.last_valid_ns,
                    force_before=self.frame['components']['spring'] if self.frame else 0,
                    force_after=self.calibrated_components['spring'],
                    last_selection=deepcopy(self.last_calibration_event))

    def select_spring(self, key):
        if key not in ('F5', 'F6'):
            raise ValueError('Seleção Spring exige F5 ou F6')
        target = self.spring_targets[key]
        previous = self.spring_applied
        changed = target != self.spring_selected
        if changed:
            # Retarget from the last multiplier actually used for a valid
            # calculation, not from an unrendered extrapolation of the ramp.
            self.spring_from = previous
            self.spring_selected = target
            self.spring_started_ns = self.now_ns if previous != target else None
        self.last_calibration_event = dict(timestamp_ns=self.now_ns, key=key,
            gain_previous=previous, gain_requested=target, gain_applied=self.spring_applied,
            changed=changed, transition_active=self.spring_gain_at(self.now_ns) != target,
            transition_started_ns=self.spring_started_ns, transition_from=self.spring_from,
            duration_ms=300, output_enabled=self.output.enabled,
            motor_playing=self.output.playing, game_running=self.game_running,
            latched=self.latched, hard_fault=self.hard_fault, evdev_level=self.output.last_sent)

    def enter_latch(self, reason):
        """Freeze evidence before any stop I/O; later discards cannot erase it."""
        if self.latched:
            return
        category = ('calculation' if reason in (
            'watchdog_calculation_stale', 'invalid_or_stale_calculation',
            'calculation_clock_not_advancing') else
            'game' if reason == 'game_exit' else
            'keyboard' if reason in ('keyboard_loss', 'F9_or_keyboard_loss') else
            'deadline' if reason == 'supervised_session_deadline' else
            'output' if reason == 'output_exception' else 'operator')
        event = dict(timestamp_ns=self.now_ns, trigger=reason, category=category,
                     action=self.action, game_running=self.game_running,
                     game_identity=deepcopy(self.game_identity),
                     previous_game_identity=deepcopy(self.previous_game_identity),
                     last_generation=self.last_generation, last_valid_ns=self.last_valid_ns,
                     age_ns=None if self.last_valid_ns is None else self.now_ns-self.last_valid_ns,
                     stop_epoch=self.stop_epoch, minimum_epoch=self.minimum_epoch,
                     incoming_frame=deepcopy(self.incoming_frame),
                     last_pid_command=deepcopy(self.last_pid_command),
                     previous_stop_reason=self.last_stop_reason, rejected=self.rejected,
                     output_before=dict(enabled=self.output.enabled, playing=self.output.playing,
                                        closed=self.output.closed, level=self.output.last_sent))
        self.last_latch = event
        if self.first_latch is None:
            self.first_latch = event
        self.latched = True
        self.latch_ns = self.now_ns
        self.reason = reason
        self.events += 1

    def reject(self, reason, data):
        self.rejected += 1
        self.rejected_by_reason[reason] = self.rejected_by_reason.get(reason, 0)+1
        self.last_rejection = dict(reason=reason, timestamp_ns=self.now_ns,
                                   generation=data.get('generation'),
                                   calculated_ns=data.get('calculated_ns'),
                                   stop_epoch=data.get('stop_epoch', 0),
                                   minimum_epoch=self.minimum_epoch, latched=self.latched)
        if not self.latched:
            self.reason = reason

    def stop(self, reason, *, latch=False, fatal=False):
        if latch:
            self.enter_latch(reason)
        elif not self.latched:
            self.reason = reason
        self.last_stop_reason = reason
        self.output.stop()
        self.output_stage = 'safety_stop'
        self.output.last_tick = -1e9
        if latch:
            self.output.set_enabled(False)
        self.hard_fault |= fatal

    def check(self):
        if self.last_valid_ns is not None and self.now_ns-self.last_valid_ns >= 100_000_000 and not self.latched:
            if self.output.playing or self.output.last_sent != 0:
                self.stop('watchdog_calculation_stale', latch=True)
            elif not self.idle_wait:
                # A normal PID stop/zero has already removed all output. Keep
                # it stopped, but require a post-timeout calculation to resume.
                self.last_idle_timeout = dict(timestamp_ns=self.now_ns,
                    age_ns=self.now_ns-self.last_valid_ns, last_generation=self.last_generation,
                    previous_stop_reason=self.last_stop_reason, minimum_epoch=self.minimum_epoch)
                self.idle_wait = True
                self.resume_after_ns = self.now_ns
                self.idle_timeouts += 1
                self.stop('watchdog_idle_awaiting_fresh_frame')
        if (self.output.test_limit_s is not None and self.output.first_active is not None and
                self.now_ns/1e9-self.output.first_active >= self.output.test_limit_s):
            self.hard_fault = True
            if not self.latched:
                self.stop('supervised_session_deadline', latch=True, fatal=True)

    def apply(self, action, data, now_ns):
        if type(now_ns) is not int or now_ns < self.now_ns:
            raise ValueError('Relógio supervisionado regressivo')
        self.now_ns = now_ns
        if self.closed:
            raise ValueError('Política encerrada')
        self.action = action
        before_plays, before_playing = self.output.play_requests, self.output.playing
        self.incoming_frame = ({k: data.get(k) for k in (
            'generation', 'calculated_ns', 'stop_epoch')} if action == 'frame' else None)
        try:
            if action == 'frame':
                # Retain the producer's last command even if its frame is discarded.
                self.last_pid_command = deepcopy(data.get('last_pid_command', self.last_pid_command))
                self.stop_epoch = max(self.stop_epoch, data.get('stop_epoch', 0))
            elif action == 'stop':
                self.stop_epoch = max(self.stop_epoch, data.get('epoch', 0))
            if self.measurement_start_ns is None:
                self.measurement_start_ns = now_ns
            # Stop has priority even when it arrives at the watchdog boundary.
            # Emergency stops still latch; normal stops may enter idle waiting.
            if action != 'stop':
                self.check()
            if action == 'frame':
                generation, calculated = data['generation'], data['calculated_ns']
                components = data['components']
                # An invalidated packet cannot command output or refresh the
                # watchdog. Its age/generation must not cause a second fault.
                if data.get('stop_epoch', 0) < self.minimum_epoch:
                    self.reject('discarded_pre_stop_frame', data)
                elif (type(generation) is not int or generation <= self.last_generation or
                        type(calculated) is not int or calculated > now_ns or
                        set(components) != set(OUTPUT_COMPONENTS) or
                        not all(math.isfinite(v) for v in components.values())):
                    self.reject('invalid_or_stale_calculation', data)
                    self.stop('invalid_or_stale_calculation', latch=True)
                elif self.idle_wait and calculated < self.resume_after_ns:
                    self.last_generation = generation
                    self.reject('awaiting_post_idle_calculation', data)
                elif now_ns-calculated >= 100_000_000:
                    self.last_generation = generation
                    self.reject('stale_during_idle_wait' if self.idle_wait else 'invalid_or_stale_calculation', data)
                    if not self.idle_wait:
                        self.stop('invalid_or_stale_calculation', latch=True)
                else:
                    self.last_generation = generation
                    if calculated < self.arm_after_ns:
                        self.reject('awaiting_post_arm_calculation', data)
                    elif self.last_valid_ns is not None and calculated <= self.last_valid_ns:
                        self.reject('calculation_clock_not_advancing', data)
                        self.stop('calculation_clock_not_advancing', latch=True)
                    else:
                        effective = dict(self.effect_gains, spring=self.spring_gain_at(now_ns))
                        calibrated = calibrate_components(components, effective)
                        calibrated_total = math.fsum(calibrated.values())
                        if not math.isfinite(calibrated_total):
                            raise ValueError('Overflow da soma calibrada')
                        self.frame = data
                        self.spring_applied = effective['spring']
                        self.calibrated_components, self.calibrated_total = calibrated, calibrated_total
                        self.last_valid_ns = calculated
                        self.accepted += 1
                        self.latency_ns = now_ns-calculated
                        self.max_latency_ns = max(self.max_latency_ns, self.latency_ns)
                        if not self.latched and self.game_running:
                            if self.idle_wait:
                                self.idle_wait = False
                                self.idle_recoveries += 1
                            before = self.output.writes
                            previous_tick = self.output.last_tick
                            self.output.tick({k: v*self.polarity for k, v in calibrated.items()})
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
                                    calibrated_total == 0 else 'quantized_or_rate_limited_zero')
                        elif not self.game_running and not self.latched:
                            self.reason = 'waiting_for_DiRT5_process'
            elif action == 'stop':
                self.minimum_epoch = max(self.minimum_epoch, data.get('epoch', 0))
                self.stop(data['reason'], latch=data.get('latch', False), fatal=data.get('fatal', False))
                self.check()
            elif action == 'strength':
                self.output.set_strength(validate_percent(data['percent']))
                if not self.latched:
                    self.output_stage = 'strength_change_awaiting_frame'
                if self.output.strength_percent == 0 and not self.latched:
                    self.reason = 'strength_zero'
            elif action == 'spring_select':
                self.select_spring(data['key'])  # Never ticks or renews an old force frame.
            elif action == 'arm':
                if (not self.hard_fault and self.game_running and
                        (self.latch_ns is None or data['requested_ns'] > self.latch_ns)):
                    self.latched = False
                    self.idle_wait = False
                    self.last_valid_ns = None
                    self.arm_after_ns = now_ns
                    self.output.set_enabled(True)
                    self.reason = 'explicit_arm_awaiting_fresh_calculation'
            elif action == 'poll':
                pass
            elif action == 'game':
                self.previous_game_identity = self.game_identity
                self.game_identity = deepcopy(data.get('identity'))
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
            self.enter_latch('output_exception')
            self.hard_fault = True
            try:
                self.output.close()
            finally:
                if self.output.last_sent == 0:
                    self.spring_command_gain = None
                self.closed = True
            raise
        self.upload_times.extend([now_ns]*(self.output.writes-self.observed_uploads))
        self.play_times.extend([now_ns]*(self.output.play_requests-self.observed_plays))
        self.observed_uploads, self.observed_plays = self.output.writes, self.output.play_requests
        if self.output.play_requests > before_plays and action == 'frame':
            self.spring_command_gain = self.spring_applied
        for _ in range(self.output.play_requests-before_plays):
            self.command_stats.record(now_ns, self.output.last_sent, self.output.cap,
                bool(self.output.last_mix and self.output.last_mix.clipped),
                self.spring_command_gain if self.spring_command_gain is not None else self.spring_applied,
                self.spring_selected)
        if before_playing and not self.output.playing:
            self.spring_command_gain = None
            self.command_stats.record(now_ns, 0, self.output.cap, False,
                                      self.spring_applied, self.spring_selected)
        return self.snapshot()

    def force_pipeline(self, raw_total):
        """Grandezas comandadas; não é medição do torque nem leitura do ganho global."""
        gain = self.output.cap/32767
        after_gain = self.calibrated_total*self.polarity*gain if raw_total is not None else None
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
                    effect_calibration=dict(gains={k: self.spring_applied if k == 'spring' else self.effect_gains.get(k, 1.0)
                                                   for k in OUTPUT_COMPONENTS},
                        components=self.calibrated_components.copy(), total=self.calibrated_total),
                    spring_calibration=self.spring_diagnostic(),
                    command_statistics=self.command_stats.snapshot(self.now_ns),
                    force_pipeline=self.force_pipeline(raw_total),
                    limited_force=mix.limited if mix else 0,
                    game_force=self.frame.get('game_force', {}) if self.frame else {},
                    effects=self.frame.get('effects', []) if self.frame else [],
                    mix=asdict(mix) if mix else None, evdev_level=self.output.last_sent,
                    uploads=self.output.writes, deduplicated=self.deduplicated,
                    rate_limited=self.rate_limited,
                    plays=self.output.play_requests, renewals=self.output.renewals,
                    accepted=self.accepted, rejected=self.rejected, safety_events=self.events,
                    transient_recovery=dict(waiting=self.idle_wait, resume_after_ns=self.resume_after_ns,
                        timeouts=self.idle_timeouts, recoveries=self.idle_recoveries,
                        last_timeout=deepcopy(self.last_idle_timeout)),
                    safety_diagnostic=dict(first_latch=deepcopy(self.first_latch),
                        last_latch=deepcopy(self.last_latch), last_rejection=deepcopy(self.last_rejection),
                        rejected_by_reason=dict(self.rejected_by_reason),
                        last_stop_reason=self.last_stop_reason, last_pid_command=deepcopy(self.last_pid_command),
                        stop_epoch=self.stop_epoch, minimum_epoch=self.minimum_epoch,
                        last_valid_ns=self.last_valid_ns),
                    latency_ns=self.latency_ns, max_latency_ns=self.max_latency_ns,
                    age_ns=None if self.last_valid_ns is None else self.now_ns-self.last_valid_ns,
                    last_generation=self.last_generation, clipped=self.output.saturation_ticks)
