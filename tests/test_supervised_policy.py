"""Relógio simulado, MockBackend e falhas injetadas: nenhuma saída física."""
import unittest
from unittest.mock import patch

from test_ffb_audit import ROOT
from dfgt_bridge.backends.mock import MockBackend
from dfgt_bridge.ffb.effects import OUTPUT_COMPONENTS
from dfgt_bridge.supervised import SupervisedPolicy


class SupervisedPolicyTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.backend = MockBackend(clock=lambda: self.now/1e9)
        self.policy = SupervisedPolicy(self.backend)
        self.generation = 0
        self.addCleanup(self.close)

    def close(self):
        if not self.policy.closed:
            self.policy.apply('close', {}, self.now)

    def frame(self, level=32767, *, dt=25_000_000, **extra):
        self.now += dt
        self.generation += 1
        data = dict(generation=self.generation, calculated_ns=self.now,
                    components=dict.fromkeys(OUTPUT_COMPONENTS, 0), **extra)
        data['components']['constant'] = level
        return self.policy.apply('frame', data, self.now)

    def command(self, action, data=None):
        self.now += 1
        return self.policy.apply(action, data or {}, self.now)

    def reach(self, level=32767):
        for _ in range(12):
            self.frame(level)

    def test_default_ten_percent_and_conservative_initial_slew(self):
        first = self.frame()
        self.assertEqual(first['strength'], 10)
        self.assertEqual(first['maximum'], 20)
        self.assertLessEqual(first['evdev_level'], 365)
        self.reach()
        self.assertEqual(self.backend.level, 3277)

    def test_increasing_strength_is_gradual_and_capped(self):
        self.reach()
        self.command('strength', dict(percent=15))
        before = self.backend.level
        after = self.frame()['evdev_level']
        self.assertLessEqual(after-before, 410)
        self.command('strength', dict(percent=100))
        self.reach()
        self.reach()
        self.assertEqual(self.policy.output.strength_percent, 20)
        self.assertEqual(self.backend.level, 6553)

    def test_decreasing_limit_bypasses_rate_gate_and_slew(self):
        self.reach()
        self.command('strength', dict(percent=5))
        self.assertEqual(self.backend.level, 1638)
        self.command('strength', dict(percent=0))
        self.assertEqual(self.backend.level, 0)

    def test_polarity_inverts_all_families_before_final_mix(self):
        self.policy.polarity = -1
        self.reach()
        self.assertEqual(self.backend.level, -3277)

    def test_reversal_is_bounded_and_passes_through_near_zero(self):
        self.reach()
        old = self.backend.level
        values = []
        for _ in range(20):
            new = self.frame(-32768)['evdev_level']
            self.assertLessEqual(abs(new-old), 410)
            values.append(new)
            old = new
        self.assertLess(values[-1], 0)
        self.assertTrue(any(abs(v) < 410 for v in values))

    def test_zero_and_disable_are_immediate(self):
        self.reach()
        self.assertEqual(self.frame(0, dt=1)['evdev_level'], 0)
        self.frame()
        value = self.command('stop', dict(reason='disable_actuators', epoch=1))
        self.assertEqual(value['evdev_level'], 0)
        self.assertEqual(self.backend.level, 0)

    def test_f9_does_not_toggle_and_strength_cannot_rearm(self):
        self.reach()
        self.command('stop', dict(reason='F9', latch=True))
        self.command('stop', dict(reason='F9', latch=True))
        self.command('strength', dict(percent=20))
        self.reach()
        self.assertTrue(self.policy.latched)
        self.assertEqual(self.backend.level, 0)

    def test_explicit_arm_requires_newer_action_and_post_arm_frame(self):
        self.reach()
        old_ns = self.now
        self.command('stop', dict(reason='F9', latch=True))
        self.command('arm', dict(requested_ns=old_ns))
        self.assertTrue(self.policy.latched)
        requested = self.now+1
        self.command('arm', dict(requested_ns=requested))
        self.assertFalse(self.policy.latched)
        self.now += 1
        stale = dict(generation=100, calculated_ns=old_ns, components=dict.fromkeys(OUTPUT_COMPONENTS, 100))
        self.policy.apply('frame', stale, self.now)
        self.assertEqual(self.backend.level, 0)
        self.generation = 100
        self.assertGreater(self.frame()['evdev_level'], 0)

    def test_watchdog_does_not_depend_on_pid_arrival(self):
        self.reach()
        for _ in range(500):
            self.frame()  # Efeito infinito válido renderizado sem novos relatórios.
        self.assertFalse(self.policy.latched)
        self.assertNotEqual(self.backend.level, 0)

    def test_poll_never_renews_and_watchdog_latches_when_renderer_stalls(self):
        self.reach()
        plays = self.policy.output.play_requests
        self.now += 100_000_000
        value = self.policy.apply('poll', {}, self.now)
        self.assertEqual(self.policy.output.play_requests, plays)
        self.assertEqual(value['zero_reason'], 'watchdog_calculation_stale')
        self.assertTrue(value['latched'])
        self.assertEqual(self.backend.level, 0)
        self.frame()
        self.assertEqual(self.backend.level, 0)

    def test_no_advancing_calculation_clock_cannot_keep_force_alive(self):
        self.reach()
        last = self.now
        data = dict(generation=100, calculated_ns=last, components=dict.fromkeys(OUTPUT_COMPONENTS, 1))
        self.now += 1
        self.policy.apply('frame', data, self.now)
        self.assertTrue(self.policy.latched)
        self.assertEqual(self.backend.level, 0)

    def test_old_frame_cannot_restore_force_after_stop_barrier(self):
        self.reach()
        self.command('stop', dict(reason='reset', epoch=3))
        old = self.frame(stop_epoch=2)
        self.assertEqual(old['evdev_level'], 0)
        self.assertEqual(old['zero_reason'], 'discarded_pre_stop_frame')
        self.assertGreater(self.frame(stop_epoch=3)['evdev_level'], 0)

    def test_reuse_single_effect_and_deduplicate_repeated_force(self):
        self.reach()
        uploads = self.policy.output.writes
        for _ in range(100):
            self.frame()
        self.assertEqual(self.policy.output.writes, uploads)
        self.assertGreater(self.policy.output.renewals, 0)
        self.assertGreater(self.policy.deduplicated, 0)
        self.assertEqual({a[1].effect_id for a in self.backend.actions if a[0] == 'upload'}, {-1, 7})

    def test_high_frequency_frames_have_bounded_upload_rate_and_no_queue(self):
        for _ in range(10000):
            self.frame(dt=100_000)
        self.assertLessEqual(self.policy.output.writes, 46)
        self.assertEqual(self.policy.accepted, 10000)
        self.assertLessEqual(len(self.backend.actions), 4096)

    def test_independent_mock_deadline_stops_after_process_disappears(self):
        self.reach()
        self.now += 161_000_000  # Nenhum poll/close/tick.
        self.assertEqual(self.backend.level, 0)

    def test_unplug_or_upload_exception_closes_output_and_erases_slot(self):
        self.reach()
        with patch.object(self.backend, 'upload_effect', side_effect=OSError('disconnected')):
            with self.assertRaises(OSError):
                self.frame(-32768)
        self.assertTrue(self.policy.closed)
        self.assertTrue(self.policy.hard_fault)
        self.assertEqual(self.backend.level, 0)
        self.assertIsNone(self.backend.effect)

    def test_total_supervised_duration_is_independent_of_infinite_effect_duration(self):
        self.reach()
        self.now += 121_000_000_000
        self.policy.apply('poll', {}, self.now)
        self.command('arm', dict(requested_ns=self.now+1))
        self.assertTrue(self.policy.hard_fault)
        self.assertTrue(self.policy.latched)
        self.assertEqual(self.backend.level, 0)

    def test_invalid_numeric_frame_stops_safely(self):
        self.reach()
        data = dict(generation=100, calculated_ns=self.now+1, components=dict.fromkeys(OUTPUT_COMPONENTS, float('nan')))
        self.now += 1
        self.policy.apply('frame', data, self.now)
        self.assertEqual(self.backend.level, 0)
        self.assertTrue(self.policy.latched)

    def test_close_is_explicit_zero_and_erase(self):
        self.reach()
        self.command('close')
        self.assertEqual(self.backend.level, 0)
        self.assertIsNone(self.backend.effect)
        self.assertTrue(self.policy.closed)
