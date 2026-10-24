"""Vetores sintéticos de matemática PID, com relógio simulado e backend mock."""
import math
import struct
import unittest

from test_ffb_audit import M
from dfgt_bridge.ffb.renderer import (FORCE_SCALE, MotionEstimator, MotionState,
                                      condition_force, direction_x, signed_unit)


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

    def condition(self, kind=9, *, center=0, positive=32767, negative=32767,
                  sat_pos=65535, sat_neg=65535, deadband=0, **kwargs):
        block = self.create(kind, **kwargs)
        self.bridge.seen(bytes((0x53, block, 0)) + struct.pack(
            '<hhhHHH', center, positive, negative, sat_pos, sat_neg, deadband))
        return self.bridge.effects[block]


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


class ConditionTests(MathVectors):
    def test_positive_coefficients_restore_spring_in_both_directions(self):
        effect = self.condition()
        for x in (-1, -.25, 0, .25, 1):
            self.assertAlmostEqual(condition_force(effect, MotionState(x)), -x * FORCE_SCALE)

    def test_signed_coefficients_keep_the_actual_s16_endpoints(self):
        self.assertEqual(signed_unit(-32768), -1)
        self.assertEqual(signed_unit(32767), 1)
        self.assertAlmostEqual(signed_unit(-32767), -32767 / 32768)
        effect = self.condition(positive=-32767, negative=-32768)
        self.assertAlmostEqual(condition_force(effect, MotionState(.5)),
                               32767 / 32768 * .5 * FORCE_SCALE)
        self.assertEqual(condition_force(effect, MotionState(-.5)), -.5 * FORCE_SCALE)

    def test_displaced_center_and_both_deadband_edges(self):
        effect = self.condition(center=8192, deadband=8192)
        center, dead = 8192 / 32767, 8192 / 65535
        for x in (center-dead, center, center+dead):
            self.assertEqual(condition_force(effect, MotionState(x)), 0)
        self.assertAlmostEqual(condition_force(effect, MotionState(center+dead+.1)),
                               -.1 * FORCE_SCALE)
        self.assertAlmostEqual(condition_force(effect, MotionState(center-dead-.1)),
                               .1 * FORCE_SCALE)

    def test_asymmetric_coefficients_and_side_saturations(self):
        effect = self.condition(positive=32767, negative=-32768,
                                sat_pos=16384, sat_neg=8192)
        self.assertAlmostEqual(condition_force(effect, MotionState(1)),
                               -16384 / 65535 * FORCE_SCALE)
        self.assertAlmostEqual(condition_force(effect, MotionState(-1)),
                               -8192 / 65535 * FORCE_SCALE)

    def test_zero_coefficients_saturations_and_full_deadband(self):
        for kwargs in (dict(positive=0, negative=0), dict(sat_pos=0, sat_neg=0),
                       dict(deadband=65535)):
            effect = self.condition(**kwargs)
            for x in (-1, 0, 1):
                self.assertEqual(condition_force(effect, MotionState(x)), 0)

    def test_signed_center_full_scale_reaches_both_axis_endpoints(self):
        for raw, position in ((-32768, -1), (32767, 1)):
            effect = self.condition(center=raw)
            self.assertEqual(condition_force(effect, MotionState(position)), 0)

    def test_damper_depends_on_velocity_and_inertia_on_acceleration(self):
        damper = self.condition(10)
        inertia = self.condition(11)
        self.assertEqual(condition_force(damper, MotionState(1)), 0)
        self.assertEqual(condition_force(inertia, MotionState(1, 2, 0)), 0)
        self.assertEqual(condition_force(damper, MotionState(0, 1, 100)), -.5 * FORCE_SCALE)
        self.assertEqual(condition_force(inertia, MotionState(0, -100, -2)), .5 * FORCE_SCALE)

    def test_dynamic_conditions_apply_center_and_deadband_to_their_metric(self):
        for kind in (10, 11, 12):
            effect = self.condition(kind, center=8192, deadband=8192)
            center, dead = 8192 / 32767, 8192 / 65535
            for metric in (center, center+dead, center-dead):
                motion = MotionState(0, metric*2, metric*4)
                self.assertEqual(condition_force(effect, motion), 0)

    def test_friction_is_coulomb_not_viscous(self):
        effect = self.condition(12, positive=8192, negative=8192)
        expected = -8192
        for speed in (.01, .5, 10):
            self.assertEqual(condition_force(effect, MotionState(0, speed)), expected)
            self.assertEqual(condition_force(effect, MotionState(0, -speed)), -expected)
        self.assertEqual(condition_force(effect, MotionState(0)), 0)

    def test_rotated_condition_projects_motion_and_response(self):
        effect = self.condition(direction=4500)
        self.assertAlmostEqual(condition_force(effect, MotionState(.5)), -.25 * FORCE_SCALE)
        # Uma rotação de 180° não inverte a reação de uma mola simétrica.
        effect['direction'] = 27000
        self.assertEqual(condition_force(effect, MotionState(.5)), -.5 * FORCE_SCALE)
        effect['direction'] = 0
        self.assertEqual(condition_force(effect, MotionState(.5)), 0)

    def test_axis_conditions_are_not_rotated_and_y_is_not_a_motor(self):
        effect = self.condition(flags=1, direction=27000)
        self.assertEqual(condition_force(effect, MotionState(.5)), -.5 * FORCE_SCALE)
        effect['axes_enabled'] = 2
        self.assertEqual(condition_force(effect, MotionState(.5)), 0)

    def test_second_condition_block_does_not_overwrite_x(self):
        effect = self.condition()
        block = self.bridge.last_block
        self.bridge.seen(bytes((0x53, block, 1)) + struct.pack(
            '<hhhHHH', 32767, -32768, -32768, 1, 1, 65535))
        self.assertEqual(condition_force(effect, MotionState(.5)), -.5 * FORCE_SCALE)
        self.assertEqual(self.bridge.ignored['condition_nonsteering_axis'], 1)

    def test_condition_envelope_is_not_applied(self):
        self.condition()
        block = self.bridge.last_block
        self.bridge.seen(bytes((0x52, block)) + struct.pack('<HHHH', 0, 0, 1000, 1000))
        result = self.bridge.components(motion=MotionState(1))
        self.assertEqual(result['spring'], -32767)

    def test_extreme_dynamic_metrics_saturate_without_sign_overflow(self):
        effect = self.condition(11)
        for acceleration in (-1e12, 1e12):
            force = condition_force(effect, MotionState(0, 0, acceleration))
            self.assertEqual(force, -math.copysign(FORCE_SCALE, acceleration))
        for invalid in (0, -1, math.inf, math.nan):
            with self.assertRaises(ValueError):
                condition_force(effect, MotionState(0), acceleration_reference=invalid)


class MotionTests(unittest.TestCase):
    def test_derivatives_use_actual_sample_interval(self):
        estimator = MotionEstimator(filter_s=0)
        self.assertEqual(estimator.sample(8191.5, 0), MotionState(0))
        state = estimator.sample(8191.5 + 819.15, .1)
        self.assertAlmostEqual(state.position, .1)
        self.assertAlmostEqual(state.velocity, 1)
        self.assertAlmostEqual(state.acceleration, 10)
        state = estimator.sample(8191.5 + 2*819.15, .2)
        self.assertAlmostEqual(state.velocity, 1)
        self.assertAlmostEqual(state.acceleration, 0)

    def test_repeated_timestamps_do_not_create_infinite_derivatives(self):
        estimator = MotionEstimator()
        estimator.sample(8191.5, 0)
        self.assertEqual(estimator.sample(16383, 0), MotionState(1))
        self.assertTrue(math.isfinite(estimator.sample(16383, .01).velocity))

    def test_stale_motion_is_reset_and_raw_position_is_bounded(self):
        estimator = MotionEstimator(filter_s=0)
        estimator.sample(0, 0)
        estimator.sample(16383, .01)
        self.assertEqual(estimator.sample(1e20, 1), MotionState(1))
        estimator.reset()
        self.assertEqual(estimator.sample(-1e20, 0), MotionState(-1))

    def test_invalid_motion_is_rejected(self):
        estimator = MotionEstimator()
        for invalid in (math.nan, math.inf, -math.inf):
            with self.assertRaises(ValueError):
                estimator.sample(invalid, 0)
            with self.assertRaises(ValueError):
                MotionState(0, invalid)
        estimator.sample(0, 1)
        with self.assertRaises(ValueError):
            estimator.sample(0, .5)


if __name__ == '__main__':
    unittest.main()
