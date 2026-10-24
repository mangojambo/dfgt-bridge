"""Regressões sintéticas do núcleo novo, sem transporte ou hardware."""
import struct
import unittest

from test_ffb_audit import M


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


if __name__ == '__main__':
    unittest.main()
