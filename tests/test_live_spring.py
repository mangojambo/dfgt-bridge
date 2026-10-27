"""Synthetic live-calibration regressions, deterministic clocks and mock EVDEV."""
import json
from pathlib import Path
import struct
import tempfile
import unittest

from test_ffb_audit import ROOT
from dfgt_bridge.backends.mock import MockBackend
from dfgt_bridge.capture import canonical
from dfgt_bridge.config import ProfileStore, default_profile
from dfgt_bridge.ffb.command_stats import CommandStatistics, WINDOW_NS
from dfgt_bridge.ffb.effects import OUTPUT_COMPONENTS, PIDSteeringBridge
from dfgt_bridge.ffb.renderer import MotionState
from dfgt_bridge.physical_worker import MAX_STATUS_PACKET, Mailbox
from dfgt_bridge.supervised import SupervisedPolicy


class LiveSpringTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.backend = MockBackend(clock=lambda: self.now/1e9)
        self.policy = SupervisedPolicy(self.backend, require_game=True)
        self.policy.apply('game', dict(running=True, identity=[42, 123]), 0)
        self.addCleanup(lambda: self.policy.apply('close', {}, self.now) if not self.policy.closed else None)
        self.raw = dict.fromkeys(OUTPUT_COMPONENTS, 0)
        self.raw['spring'] = -1000

    def frame(self, dt=25_000_000, *, epoch=0, components=None, calculated=None):
        self.now += dt
        return self.policy.apply('frame', dict(generation=self.policy.last_generation+1,
            calculated_ns=self.now if calculated is None else calculated, stop_epoch=epoch,
            components=self.raw.copy() if components is None else components), self.now)

    def select(self, key):
        return self.policy.apply('spring_select', dict(key=key), self.now)

    def settle_selection(self, key):
        self.select(key)
        for _ in range(12):
            value = self.frame()
        return value

    def test_f5_and_f6_are_direct_idempotent_selectors_and_do_not_write_force(self):
        self.frame()
        plays, writes = self.policy.output.play_requests, self.policy.output.writes
        self.select('F5')
        self.assertEqual(self.select('F5')['spring_calibration']['selected'], 3)
        self.select('F6')
        started = self.policy.spring_started_ns
        self.assertEqual(self.select('F6')['spring_calibration']['selected'], 4)
        self.assertEqual(self.policy.spring_started_ns, started)
        self.assertEqual((self.policy.output.play_requests, self.policy.output.writes), (plays, writes))
        self.assertFalse(self.policy.last_calibration_event['changed'])

    def test_300ms_ramp_reaches_simulated_physical_output_without_jumps(self):
        self.frame()
        self.assertEqual(self.settle_selection('F5')['evdev_level'], 3000)
        self.select('F6')
        previous = self.policy.output.last_sent
        for step in range(1, 13):
            value = self.frame()
            gain = 3+step/12
            self.assertAlmostEqual(value['spring_calibration']['applied'], gain)
            self.assertEqual(value['effect_calibration']['components']['spring'], -1000*gain)
            self.assertEqual(value['evdev_level'], round(1000*gain))
            self.assertLessEqual(abs(value['evdev_level']-previous), 84)
            previous = value['evdev_level']
        self.assertEqual(value['spring_calibration']['applied'], 4)
        self.assertFalse(value['spring_calibration']['transition_active'])
        self.assertEqual(self.policy.effect_gains, {'spring': 3.0})

    def test_repeated_selection_during_ramp_does_not_restart_it(self):
        self.frame()
        self.select('F6')
        started = self.policy.spring_started_ns
        for _ in range(12):
            self.frame()
            self.select('F6')
            self.assertEqual(self.policy.spring_started_ns, started)
        self.assertEqual(self.policy.spring_applied, 4)

    def test_rate_gate_diagnostics_separate_calculation_gain_from_command_gain(self):
        self.frame()
        self.select('F6')
        commands = self.policy.command_stats.snapshot(self.now)['session']['commands']
        value = self.frame(dt=1_000_000)
        self.assertGreater(value['spring_calibration']['applied'], 3)
        self.assertEqual(value['spring_calibration']['command_gain'], 3)
        self.assertEqual(value['evdev_level'], 3000)
        self.assertEqual(value['command_statistics']['session']['commands'], commands)
        value = self.frame(dt=24_000_000)
        self.assertEqual(value['spring_calibration']['command_gain'], value['spring_calibration']['applied'])

    def test_custom_profile_ramp_has_exact_endpoint(self):
        policy = SupervisedPolicy(MockBackend(), effect_gains={'spring': 6.37})
        policy.apply('spring_select', dict(key='F5'), 0)
        self.assertEqual(policy.spring_gain_at(300_000_000), 3.0)
        policy.apply('close', {}, 0)

    def test_retarget_uses_last_applied_gain_instead_of_unrendered_extrapolation(self):
        self.frame()
        self.settle_selection('F5')
        self.select('F6')
        for _ in range(4):
            self.frame()
        applied = self.policy.spring_applied
        self.now += 10_000_000
        self.policy.apply('poll', {}, self.now)
        self.assertGreater(self.policy.spring_gain_at(self.now), applied)
        self.select('F5')
        self.assertEqual(self.policy.spring_from, applied)
        for step in range(1, 13):
            value = self.frame()
            self.assertAlmostEqual(value['spring_calibration']['applied'], applied+(3-applied)*step/12)
        self.assertEqual(value['evdev_level'], 3000)

    def test_f5_f6_under_latch_do_not_rearm_or_replay_pre_stop_frames(self):
        self.frame()
        self.policy.apply('stop', dict(reason='F9', latch=True, epoch=1), self.now)
        plays = self.policy.output.play_requests
        for key in ('F6', 'F5', 'F6'):
            self.assertTrue(self.select(key)['latched'])
            self.assertEqual(self.frame(epoch=0)['evdev_level'], 0)
        for _ in range(13):
            self.assertEqual(self.frame(epoch=1)['evdev_level'], 0)
        self.assertEqual(self.policy.output.play_requests, plays)
        self.assertEqual(self.policy.reason, 'F9')
        self.now += 1
        self.policy.apply('arm', dict(requested_ns=self.now), self.now)
        self.assertEqual(self.policy.output.last_sent, 0)
        self.assertEqual(self.frame(epoch=0)['evdev_level'], 0)
        self.assertEqual(self.frame(epoch=1)['evdev_level'], 4000)

    def test_selection_and_poll_never_refresh_watchdog(self):
        self.frame()
        self.select('F6')
        plays = self.policy.output.play_requests
        self.now += 100_000_000
        value = self.select('F5')
        self.assertTrue(value['latched'])
        self.assertEqual(value['zero_reason'], 'watchdog_calculation_stale')
        self.assertEqual(value['evdev_level'], 0)
        self.assertEqual(self.policy.output.play_requests, plays)
        self.assertEqual(self.frame()['evdev_level'], 0)

    def test_stop_start_and_idle_watchdog_still_require_new_valid_epoch(self):
        self.frame()
        self.policy.apply('stop', dict(reason='PID_stop_reset_disable_or_close', epoch=1), self.now)
        old = self.now
        self.now += 150_000_000
        self.policy.apply('poll', {}, self.now)
        self.select('F6')
        self.assertEqual(self.frame(epoch=1, calculated=old)['evdev_level'], 0)
        self.assertEqual(self.frame(epoch=0)['evdev_level'], 0)
        self.assertGreater(self.frame(epoch=1)['evdev_level'], 0)
        self.assertFalse(self.policy.latched)

    def test_pid_disable_pause_and_stop_remain_zero_with_spring_four_selected(self):
        engine = PIDSteeringBridge(clock=lambda: self.now/1e9)
        engine.arm()
        engine.set_report(bytes.fromhex('54090000'), 0x54)
        block = engine.last_block
        engine.write_output(bytes((0x51, block, 9))+struct.pack('<HHHHBBBHH', 0xffff, 0, 0, 0, 100, 255, 1, 9000, 0))
        engine.write_output(bytes((0x53, block, 0))+struct.pack('<hhhHHH', 0, 32767, 32767, 65535, 65535, 0))
        engine.write_output(bytes((0x5a, block, 1, 1)))
        self.select('F6')
        for stop, start in ((bytes.fromhex('5002'), bytes.fromhex('5001')),
                            (bytes.fromhex('5005'), bytes.fromhex('5006')),
                            (bytes((0x5a, block, 3, 0)), bytes((0x5a, block, 1, 1)))):
            self.assertGreater(self.frame(components=engine.components(motion=MotionState(.25)))['evdev_level'], 0)
            engine.write_output(stop)
            value = self.frame(dt=1, components=engine.components(motion=MotionState(.25)))
            self.assertEqual(value['evdev_level'], 0)
            self.assertEqual(value['spring_calibration']['force_after'], 0)
            engine.write_output(start)

    def test_other_families_stay_at_their_configured_gains(self):
        self.policy.effect_gains.update(damper=1.5, periodic=.75)
        self.raw.update(constant=300, damper=400, periodic=-200, friction=50, inertia=-40, ramp=10)
        self.select('F6')
        for _ in range(12):
            value = self.frame()
        expected = dict(self.raw, spring=-4000, damper=600, periodic=-150)
        self.assertEqual(value['effect_calibration']['components'], expected)
        self.assertEqual(value['evdev_level'], round(-sum(expected.values())))

    def test_profile_custom_gain_is_restored_in_new_session_without_file_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ProfileStore(Path(directory)/'profiles.json')
            profile = dict(default_profile(), ffb_spring_gain=1.25, ffb_effect_gains={'damper': 1.5})
            store.save('dirt5', profile)
            before = store.path.read_bytes()
            policies = []
            for _ in range(2):
                saved = store.load()
                policy = SupervisedPolicy(MockBackend(), effect_gains=dict(saved['ffb_effect_gains'], spring=saved['ffb_spring_gain']))
                policies.append(policy)
                self.assertEqual(policy.spring_selected, 1.25)
                policy.apply('spring_select', dict(key='F6'), 0)
                self.assertEqual(policy.spring_selected, 4)
                policy.apply('close', {}, 0)
            self.assertEqual(store.path.read_bytes(), before)
            self.assertEqual(store.load(), profile)

    def test_saved_reference_stays_three_across_three_four_selections_and_new_sessions(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ProfileStore(Path(directory)/'profiles.json')
            store.save('dirt5', default_profile())
            before = store.path.read_bytes()
            for _ in range(2):
                saved = store.load('dirt5')
                self.assertEqual(saved['ffb_spring_gain'], 3.0)
                policy = SupervisedPolicy(MockBackend(clock=lambda: 0),
                    effect_gains=dict(saved['ffb_effect_gains'], spring=saved['ffb_spring_gain']))
                self.assertEqual(policy.spring_selected, 3)
                self.assertEqual(policy.apply('spring_select', dict(key='F5'), 0)['spring_calibration']['selected'], 3)
                self.assertEqual(policy.apply('spring_select', dict(key='F6'), 1)['spring_calibration']['selected'], 4)
                policy.apply('close', {}, 2)
                self.assertEqual(store.path.read_bytes(), before)
            self.assertEqual(store.load('dirt5')['ffb_spring_gain'], 3.0)

    def test_command_stats_count_limit_clipping_and_recent_comparison(self):
        self.policy = SupervisedPolicy(self.backend, require_game=True, effect_gains={'spring': 2})
        self.policy.apply('game', dict(running=True, identity=[42, 123]), 0)
        self.raw['spring'] = -9000
        for _ in range(8):
            self.frame()
        self.settle_selection('F5')
        for _ in range(4):
            self.frame()
        self.select('F6')
        for _ in range(20):
            value = self.frame()
            self.assertLessEqual(abs(value['evdev_level']), 32767)
        stats = value['command_statistics']
        self.assertEqual(stats['session']['peak_abs_level'], 32767)
        self.assertEqual(stats['session_by_spring']['2']['clipped'], 0)
        self.assertGreater(stats['session_by_spring']['2']['commands'], 0)
        self.assertGreater(stats['session_by_spring']['3']['commands'], 0)
        self.assertEqual(stats['session_by_spring']['3']['clipped'], 0)
        self.assertGreater(stats['session_by_spring']['4']['at_limit'], 0)
        self.assertGreater(stats['recent']['by_spring']['4']['clipped_ratio'], 0)
        self.assertGreater(stats['recent']['by_spring']['transition']['commands'], 0)
        self.assertLessEqual(stats['session']['at_limit'], stats['session']['commands'])
        self.policy.apply('stop', dict(reason='PID_stop_reset_disable_or_close', epoch=1), self.now)
        count = self.policy.command_stats.snapshot(self.now)['session']['commands']
        self.now += WINDOW_NS+1
        value = self.policy.apply('poll', {}, self.now)
        self.assertEqual(value['command_statistics']['recent']['commands'], 0)
        self.assertIsNone(value['command_statistics']['recent']['at_limit_ratio'])
        self.assertEqual(value['command_statistics']['session']['commands'], count)

    def test_recent_history_is_bounded_and_reports_truncation(self):
        stats = CommandStatistics()
        for stamp in range(2050):
            stats.record(stamp, 32767, 32767, True, 3, 3)
        value = stats.snapshot(2050)
        self.assertEqual(value['session']['commands'], 2050)
        self.assertEqual(value['recent']['commands'], 2048)
        self.assertTrue(value['recent']['truncated'])
        self.assertEqual(stats.snapshot(WINDOW_NS+2050)['recent']['commands'], 0)

    def test_expanded_f10_with_full_pool_and_latch_fits_actual_status_mailbox(self):
        self.frame()
        self.policy.frame['effects'] = [[i, 1, True, 1.0] for i in range(1, 41)]
        self.policy.frame['game_force'] = dict(pid_gain=1, constant_levels=[[i, -32768] for i in range(1, 41)])
        self.select('F6')
        self.policy.apply('stop', dict(reason='F9', latch=True), self.now)
        value = dict(self.policy.snapshot(), first_latch_io=dict(keyboard_errors=['x'*160]*3))
        self.assertLessEqual(len(canonical(value).encode()), MAX_STATUS_PACKET)
        from types import SimpleNamespace
        ctx = SimpleNamespace(RawValue=lambda _, value: SimpleNamespace(value=value), RawArray=lambda _, n: bytearray(n))
        mailbox = Mailbox(ctx, MAX_STATUS_PACKET)
        mailbox.write(value)
        self.assertEqual(mailbox.read(), json.loads(canonical(value)))
