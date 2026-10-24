"""Deduplicação, renovação e expiração independente da saída simulada."""
import unittest

from test_ffb_audit import M
from dfgt_bridge.backends.mock import MockBackend


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.now = 0.0
        self.motor = M.PhysicalConstantMotor(clock=lambda: self.now)
        self.backend = self.motor.dev

    def settle(self, level=32767):
        for _ in range(3):
            self.motor.tick(level)
            self.now += .025

    def test_unchanged_force_renews_without_reupload_and_remains_active(self):
        self.settle()
        uploads = self.motor.writes
        for _ in range(200):
            self.motor.tick(32767)
            self.assertTrue(self.backend.playing)
            self.assertEqual(self.backend.level, self.motor.cap)
            self.now += .025
        self.assertEqual(self.motor.writes, uploads)
        self.assertGreater(self.motor.renewals, 20)

    def test_stop_and_restart_same_level_reuses_uploaded_effect(self):
        self.motor.tick(1000)
        self.motor.stop()
        self.assertEqual(self.backend.level, 0)
        self.now += .03
        self.motor.tick(1000)
        self.assertEqual(self.motor.writes, 1)
        self.assertEqual(self.motor.play_requests, 2)
        self.assertEqual(self.backend.level, round(1000*self.motor.cap/32767))

    def test_zero_between_two_starts_is_not_lost_to_rate_limit(self):
        self.motor.tick(32767)
        self.motor.tick(0)
        self.assertFalse(self.backend.playing)
        self.now += .025
        self.motor.tick(32767)
        self.assertTrue(self.backend.playing)

    def test_no_tick_simulates_crash_and_mock_replay_expires_independently(self):
        self.motor.tick(32767)
        self.assertTrue(self.backend.playing)
        self.now = .16
        self.assertFalse(self.backend.playing)
        self.assertEqual(self.backend.level, 0)
        self.assertTrue(self.motor.playing)  # Nenhum tick/processo executou cleanup.

    def test_resume_after_expiry_uses_latest_force_and_restarts_slew(self):
        self.settle()
        self.now = 1
        self.assertEqual(self.backend.level, 0)
        self.motor.tick(-32767)
        self.assertLess(self.backend.level, 0)
        self.assertLessEqual(abs(self.backend.level), max(512, self.motor.cap//2))

    def test_high_frequency_updates_have_no_command_backlog(self):
        for index in range(10000):
            self.now = index/1000000
            self.motor.tick(32767 if index % 2 else 16000)
        self.assertEqual(self.motor.writes, 1)
        self.assertLess(len(self.backend.actions), 10)
        self.now = .03
        self.motor.tick(-32767)
        self.assertLessEqual(self.motor.last_sent, 0)

    def test_mock_history_is_bounded_and_does_not_queue_pending_commands(self):
        backend = MockBackend(clock=lambda: self.now, max_actions=16)
        motor = M.PhysicalConstantMotor(backend, clock=lambda: self.now)
        for index in range(1000):
            self.now = index*.025
            motor.tick(1000 if index % 2 else 2000)
        self.assertEqual(len(backend.actions), 16)
        self.assertGreater(backend.dropped_actions, 1000)
        self.assertTrue(backend.playing)

    def test_immediate_reduced_limit_bypasses_dedup_and_normal_rate_gate(self):
        self.motor = M.PhysicalConstantMotor(strength_percent=60, clock=lambda: self.now)
        self.settle()
        self.motor.set_strength(5)
        self.assertLessEqual(abs(self.motor.dev.level), self.motor.cap)
        self.motor.set_strength(0)
        self.assertEqual(self.motor.dev.level, 0)

    def test_close_is_idempotent_and_cannot_be_rearmed(self):
        self.settle()
        self.motor.close()
        self.motor.close()
        self.motor.tick(32767)
        self.assertEqual(self.backend.level, 0)
        self.assertEqual(sum(action[0] == 'erase' for action in self.backend.actions), 1)
        with self.assertRaises(ValueError):
            self.motor.set_enabled(True)
