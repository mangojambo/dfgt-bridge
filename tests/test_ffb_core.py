"""Regressões sintéticas do núcleo novo, sem transporte ou hardware."""
import struct
import unittest

from test_ffb_audit import M
from dfgt_bridge.backends.mock import MockBackend
from dfgt_bridge.ffb.safety import ConstantOutput


class EffectTimingTests(unittest.TestCase):
    def setUp(self):
        self.now = 0.0
        self.bridge = M.PIDSteeringBridge(clock=lambda: self.now)
        self.bridge.arm()

    def constant(self, duration=0xffff, delay=0):
        self.bridge.set_report(bytes((0x54, 1, 0, 0)), 0x54)
        block = self.bridge.last_block
        self.bridge.seen(bytes((0x51, block, 1)) + struct.pack(
            '<HHHHBBBHH', duration, 0, 0, delay, 100, 255, 4, 9000, 0))
        self.bridge.seen(bytes((0x55, block)) + struct.pack('<h', 16000))
        self.bridge.seen(bytes((0x5a, block, 1, 1)))
        return block

    def test_finite_duration_stops_at_boundary(self):
        block = self.constant(duration=100)
        self.now = .099
        self.assertEqual(self.bridge.raw_level(), 16000)
        self.now = .1
        self.assertEqual(self.bridge.raw_level(), 0)
        self.assertFalse(self.bridge.effects[block]['running'])

    def test_zero_duration_is_not_infinite(self):
        self.constant(duration=0)
        self.assertEqual(self.bridge.raw_level(), 0)

    def test_infinite_duration_survives_long_silence_until_stop(self):
        block = self.constant()
        self.now = 1000.0
        self.assertEqual(self.bridge.raw_level(), 16000)
        self.bridge.seen(bytes((0x5a, block, 3, 0)))
        self.assertEqual(self.bridge.raw_level(), 0)

    def test_delay_longer_than_old_watchdog_is_honored(self):
        self.constant(duration=100, delay=500)
        self.now = .3
        self.assertEqual(self.bridge.raw_level(), 0)
        self.now = .5
        self.assertEqual(self.bridge.raw_level(), 16000)
        self.now = .7
        self.assertEqual(self.bridge.raw_level(), 0)

    def test_parameter_update_does_not_extend_duration(self):
        block = self.constant(duration=100)
        self.now = .09
        self.bridge.seen(bytes((0x55, block)) + struct.pack('<h', 8000))
        self.assertEqual(self.bridge.raw_level(), 8000)
        self.now = .1
        self.assertEqual(self.bridge.raw_level(), 0)

    def test_condition_remains_active_without_parameter_updates(self):
        self.bridge.set_report(bytes((0x54, 9, 0, 0)), 0x54)
        block = self.bridge.last_block
        self.bridge.seen(bytes((0x53, block, 0)) + struct.pack(
            '<hhhHHH', 0, 16000, 16000, 65535, 65535, 0))
        self.bridge.seen(bytes((0x5a, block, 1, 1)))
        initial = self.bridge.raw_level(12000)
        self.assertNotEqual(initial, 0)
        self.now = 1.0
        self.assertEqual(self.bridge.raw_level(12000), initial)


class OutputSafetyTests(unittest.TestCase):
    def setUp(self):
        self.now = 0.0
        self.motor = ConstantOutput(strength_percent=60, clock=lambda: self.now)
        self.dev = self.motor.dev

    def establish(self, sign=1):
        for _ in range(2):
            self.motor.tick(sign * 32767)
            self.now += .03

    def test_default_backend_is_simulated_and_replay_stays_short(self):
        self.assertIsInstance(self.dev, MockBackend)
        self.motor.tick(32767)
        request = next(x[1] for x in self.dev.actions if x[0] == 'upload')
        self.assertEqual(request.duration_ms, 160)
        self.assertEqual(request.direction, 0x4000)

    def test_reduction_applies_in_setter_for_both_polarities(self):
        for sign in (-1, 1):
            with self.subTest(sign=sign):
                self.motor.stop()
                self.establish(sign)
                self.motor.set_strength(5)
                self.assertEqual(self.motor.last_sent, sign * self.motor.cap)
                self.assertEqual(self.dev.actions[-3][1].level, sign * self.motor.cap)
                self.motor.set_strength(60)

    def test_zero_intensity_stops_without_waiting_for_tick(self):
        self.establish()
        self.motor.set_strength(0)
        self.assertEqual(self.motor.last_sent, 0)
        self.assertFalse(self.motor.playing)
        self.assertIn(('write', (0x15, 7, 0)), self.dev.actions)
        uploads = self.motor.writes
        self.now += .1
        self.motor.tick(32767)
        self.assertEqual(self.motor.writes, uploads)

    def test_zero_request_stops_inside_rate_interval(self):
        self.motor.tick(32767)
        self.now += .001
        self.motor.tick(0)
        self.assertFalse(self.motor.playing)
        self.assertEqual(self.motor.last_sent, 0)

    def test_zero_components_stop_inside_rate_interval(self):
        self.motor.tick(32767)
        self.now += .001
        self.motor.tick({'constant': 0, 'spring': 0})
        self.assertFalse(self.motor.playing)

    def test_regular_updates_remain_rate_limited(self):
        self.motor.tick(32767)
        self.now += .001
        self.motor.tick(-32767)
        self.assertEqual(self.motor.writes, 1)

    def test_final_cap_after_slew_with_outdated_previous_value(self):
        self.motor.set_strength(1)
        self.motor.last_sent = 30000
        self.motor.tick(-32767)
        self.assertLessEqual(abs(self.motor.last_sent), self.motor.cap)

    def test_every_request_respects_current_cap_during_strength_changes(self):
        for strength in (60, 5, 40, 0, 1, 70, 10):
            self.motor.set_strength(strength)
            for level in (32767, -32767, 10000, 0):
                before = len(self.dev.actions)
                self.now += .03
                self.motor.tick(level)
                for action in self.dev.actions[before:]:
                    if action[0] == 'upload':
                        self.assertLessEqual(abs(action[1].level), self.motor.cap)
                self.assertLessEqual(abs(self.motor.last_sent), self.motor.cap)


if __name__ == '__main__':
    unittest.main()
