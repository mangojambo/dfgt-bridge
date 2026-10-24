"""Vetores sintéticos de matemática PID, com relógio simulado e backend mock."""
import math
import struct
import unittest

from test_ffb_audit import M
from dfgt_bridge.ffb.renderer import direction_x


class MathVectors(unittest.TestCase):
    def setUp(self):
        self.now = 0.0
        self.bridge = M.PIDSteeringBridge(clock=lambda: self.now)
        self.bridge.arm()

    def create(self, kind=1, *, direction=9000, flags=4, duration=0xffff,
               delay=0, gain=100):
        self.bridge.set_report(bytes((0x54, kind, 0, 0)), 0x54)
        block = self.bridge.last_block
        self.configure(block, kind, direction, flags, duration, delay, gain)
        self.bridge.seen(bytes((0x5a, block, 1, 1)))
        return block

    def configure(self, block, kind=1, direction=9000, flags=4,
                  duration=0xffff, delay=0, gain=100):
        self.bridge.seen(bytes((0x51, block, kind)) + struct.pack(
            '<HHHHBBBHH', duration, 0, 0, delay, gain, 255, flags, direction, 0))

    def constant(self, level=16000, **kwargs):
        block = self.create(**kwargs)
        self.bridge.seen(bytes((0x55, block)) + struct.pack('<h', level))
        return block


class DirectionTests(MathVectors):
    def test_cardinal_directions_have_exact_x_projection(self):
        for angle, expected in ((0, 0), (9000, 1), (18000, 0), (27000, -1)):
            self.assertEqual(direction_x(angle), expected)

    def test_signed_level_and_opposite_direction_compose(self):
        for level, angle, expected in ((16000, 9000, 16000),
                                       (16000, 27000, -16000),
                                       (-16000, 9000, -16000),
                                       (-16000, 27000, 16000)):
            with self.subTest(level=level, angle=angle):
                self.bridge.effects.clear()
                self.constant(level, direction=angle)
                self.assertEqual(self.bridge.raw_level(), expected)

    def test_diagonal_is_projected(self):
        self.constant(direction=4500)
        self.assertEqual(self.bridge.raw_level(), round(16000 / math.sqrt(2)))

    def test_direction_enable_ignores_axes_flags(self):
        self.constant(flags=4, direction=9000)
        self.assertEqual(self.bridge.raw_level(), 16000)

    def test_x_axis_mode_ignores_angle(self):
        self.constant(flags=1, direction=27000)
        self.assertEqual(self.bridge.raw_level(), 16000)

    def test_disabled_and_y_only_axes_never_drive_steering(self):
        for flags in (0, 2):
            self.bridge.effects.clear()
            self.constant(flags=flags)
            self.assertEqual(self.bridge.raw_level(), 0)

    def test_periodic_and_ramp_follow_direction(self):
        periodic = self.create(5, direction=27000)
        self.bridge.seen(bytes((0x5d, periodic)) + struct.pack('<HhHH', 8000, 0, 9000, 100))
        self.assertEqual(self.bridge.raw_level(), -8000)
        self.bridge.effects.clear()
        ramp = self.create(2, direction=27000)
        self.bridge.seen(bytes((0x58, ramp, 255, 255)))
        self.assertEqual(self.bridge.raw_level(), -32767)

    def test_wire_angle_units_and_limits(self):
        self.assertAlmostEqual(direction_x(35900), math.sin(math.radians(359)))
        for invalid in (-1, 36000, math.nan, math.inf):
            with self.assertRaises(ValueError):
                direction_x(invalid)

    def test_invalid_direction_report_does_not_change_previous_state(self):
        block = self.constant()
        self.configure(block, direction=65535)
        self.assertEqual(self.bridge.raw_level(), 16000)
        self.assertEqual(self.bridge.ignored['invalid_direction'], 1)


if __name__ == '__main__':
    unittest.main()
