"""Vetores sintéticos de matemática PID, com relógio simulado e backend mock."""
import math
import struct
import unittest

from test_ffb_audit import M
from dfgt_bridge.ffb.renderer import (FORCE_SCALE, MotionEstimator, MotionState,
                                      condition_force, direction_x, periodic_wave,
                                      signed_unit)
from dfgt_bridge.ffb.mixer import mix_components
from dfgt_bridge.ffb.safety import ConstantOutput


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


class EnvelopeTests(MathVectors):
    def envelope(self, block, attack=8000, fade=4000, attack_ms=100, fade_ms=200):
        self.bridge.seen(bytes((0x52, block)) + struct.pack(
            '<HHHH', attack, fade, attack_ms, fade_ms))

    def test_absolute_attack_sustain_fade_with_exact_checkpoints(self):
        block = self.constant(16000, duration=1000)
        self.envelope(block)
        for instant, expected in ((0, 8000), (.05, 12000), (.1, 16000),
                                  (.8, 16000), (.9, 10000), (1, 0)):
            self.now = instant
            self.assertEqual(self.bridge.raw_level(), expected)

    def test_negative_level_preserves_sign_through_envelope(self):
        block = self.constant(-16000, duration=1000)
        self.envelope(block)
        self.assertEqual(self.bridge.raw_level(), -8000)
        self.now = .9
        self.assertEqual(self.bridge.raw_level(), -10000)

    def test_envelope_above_sustain_is_not_truncated_to_a_gain(self):
        block = self.constant(4000, duration=1000)
        self.envelope(block, attack=12000, fade=16000)
        self.assertEqual(self.bridge.raw_level(), 12000)
        self.now = .05
        self.assertEqual(self.bridge.raw_level(), 8000)
        self.now = .9
        self.assertEqual(self.bridge.raw_level(), 10000)

    def test_periodic_offset_is_not_scaled_by_the_envelope(self):
        block = self.create(5)
        self.bridge.seen(bytes((0x5d, block)) + struct.pack('<HhHH', 16000, 4000, 9000, 100))
        self.envelope(block, attack=8000)
        self.assertEqual(self.bridge.raw_level(), 12000)
        self.now = .1
        self.assertEqual(self.bridge.raw_level(), 20000)

    def test_effect_and_device_gain_are_applied_after_absolute_envelope(self):
        block = self.constant(16000, gain=50)
        self.envelope(block, attack=8000)
        self.bridge.seen(bytes((0x59, 128)))
        self.assertEqual(self.bridge.raw_level(), round(8000 * .5 * 128/255))

    def test_start_delay_precedes_attack_and_does_not_eat_its_time(self):
        block = self.constant(16000, duration=1000, delay=500)
        self.envelope(block)
        self.now = .4
        self.assertEqual(self.bridge.raw_level(), 0)
        self.now = .5
        self.assertEqual(self.bridge.raw_level(), 8000)
        self.now = .55
        self.assertEqual(self.bridge.raw_level(), 12000)

    def test_infinite_effect_has_attack_and_no_automatic_fade(self):
        block = self.constant(16000)
        self.envelope(block, attack=8000, fade=0)
        self.now = .05
        self.assertEqual(self.bridge.raw_level(), 12000)
        self.now = 1000
        self.assertEqual(self.bridge.raw_level(), 16000)

    def test_overlap_fits_continuously_without_multiplying_two_envelopes(self):
        block = self.constant(16000, duration=100)
        self.envelope(block, attack=0, fade=0, attack_ms=200, fade_ms=200)
        for instant, expected in ((.025, 8000), (.05, 16000), (.075, 8000), (.1, 0)):
            self.now = instant
            self.assertEqual(self.bridge.raw_level(), expected)

    def test_zero_times_bypass_attack_and_fade_levels(self):
        block = self.constant(16000, duration=1000)
        self.envelope(block, attack=0, fade=0, attack_ms=0, fade_ms=0)
        self.assertEqual(self.bridge.raw_level(), 16000)
        self.now = .999
        self.assertEqual(self.bridge.raw_level(), 16000)

    def test_ramp_reserves_attack_and_fade_intervals(self):
        block = self.create(2, duration=1000)
        self.bridge.seen(bytes((0x58, block, 0, 255)))
        self.envelope(block, attack=0, fade=0, attack_ms=200, fade_ms=200)
        self.now = .5
        self.assertEqual(self.bridge.raw_level(), round(32767/2))
        self.now = .8
        self.assertEqual(self.bridge.raw_level(), 32767)
        self.now = .9
        self.assertEqual(self.bridge.raw_level(), round(32767/2))

    def test_ramp_overlapping_envelope_has_continuous_meeting_point(self):
        block = self.create(2, duration=100)
        self.bridge.seen(bytes((0x58, block, 0, 255)))
        self.envelope(block, attack=0, fade=0, attack_ms=200, fade_ms=200)
        self.now = .05
        self.assertEqual(self.bridge.raw_level(), round(32767/2))
        self.now = .050001
        self.assertLess(abs(self.bridge.raw_level() - round(32767/2)), 2)

    def test_out_of_range_envelope_and_periodic_leave_previous_state_intact(self):
        block = self.constant()
        self.envelope(block, attack=65535)
        self.assertEqual(self.bridge.effects[block]['attack_time'], 0)
        self.assertEqual(self.bridge.ignored['invalid_envelope'], 1)
        block = self.create(5)
        self.bridge.seen(bytes((0x5d, block)) + struct.pack('<HhHH', 65535, 0, 0, 100))
        self.assertEqual(self.bridge.effects[block]['periodic_magnitude'], 0)
        self.assertEqual(self.bridge.ignored['invalid_periodic'], 1)

    def test_reference_waveforms_at_quarter_periods(self):
        vectors = {4: (1, 1, -1, -1), 5: (0, 1, 0, -1),
                   6: (1, 0, -1, 0), 7: (-1, -.5, 0, .5),
                   8: (1, .5, 0, -.5)}
        for kind, values in vectors.items():
            for phase, expected in zip((0, .25, .5, .75), values):
                self.assertAlmostEqual(periodic_wave(kind, phase), expected)


class MixingTests(MathVectors):
    def test_no_family_clipping_before_opposite_force_cancellation(self):
        self.constant(32767)
        self.constant(32767)
        self.condition()
        result = self.bridge.components(motion=MotionState(1))
        self.assertEqual(result['constant'], 65534)
        self.assertEqual(result['spring'], -32767)
        self.assertEqual(mix_components(result).limited, 32767)

    def test_multiple_opposite_constants_cancel_exactly(self):
        for level in (32000, -32000, 25000, -25000):
            self.constant(level)
        self.assertEqual(self.bridge.raw_level(), 0)

    def test_gain_is_applied_to_sum_before_final_limit(self):
        result = mix_components({'constant': 40000, 'spring': -35000}, gain=.5, limit=10000)
        self.assertEqual(result.requested, 2500)
        self.assertEqual(result.limited, 2500)
        self.assertFalse(result.clipped)

    def test_positive_and_negative_clipping_are_symmetric_and_observable(self):
        for sign in (-1, 1):
            result = mix_components({'constant': sign*40000}, limit=10000)
            self.assertEqual(result.unlimited_requested, sign*40000)
            self.assertEqual(result.limited, sign*10000)
            self.assertTrue(result.clipped)

    def test_cancellation_preserves_small_residual_without_order_dependence(self):
        for values in ({'constant': 1e16, 'spring': 1, 'periodic': -1e16},
                       {'periodic': -1e16, 'spring': 1, 'constant': 1e16}):
            self.assertEqual(mix_components(values).limited, 1)

    def test_rounding_happens_only_after_cross_family_sum(self):
        self.constant(1, gain=40)
        periodic = self.create(5, gain=40)
        self.bridge.seen(bytes((0x5d, periodic)) + struct.pack('<HhHH', 0, 1, 0, 100))
        self.assertEqual(self.bridge.raw_level(), 1)  # round(.4+.4), não round(.4)+round(.4).

    def test_six_simultaneous_effects_reach_mock_with_correct_cancellation(self):
        self.constant(16384)
        self.condition(9)
        self.condition(10)
        self.condition(11)
        self.condition(12, positive=8192)
        periodic = self.create(5)
        self.bridge.seen(bytes((0x5d, periodic)) + struct.pack('<HhHH', 8000, 0, 9000, 100))
        result = self.bridge.components(motion=MotionState(.25, .5, -1))
        self.assertEqual(math.fsum(result.values()), 8000.25)
        motor = ConstantOutput(strength_percent=20, clock=lambda: self.now)
        motor.tick(result)
        self.assertEqual(motor.last_sent, 1600)
        self.assertFalse(motor.last_mix.clipped)

    def test_effect_device_and_user_gain_are_each_applied_once(self):
        self.constant(16000, gain=25)
        self.bridge.seen(bytes((0x59, 128)))
        motor = ConstantOutput(strength_percent=50, boost=2, clock=lambda: self.now)
        motor.tick(self.bridge.components())
        self.assertEqual(motor.last_sent, round(16000 * .25 * 128/255 * 2 * motor.cap/32767))

    def test_condition_saturation_precedes_effect_gain(self):
        self.condition(sat_pos=16384, gain=50)
        result = self.bridge.components(motion=MotionState(1))
        self.assertAlmostEqual(result['spring'], -16384/65535 * 32767 * .5)

    def test_optional_reserve_preserves_both_halves_of_transients_under_heavy_spring(self):
        for transient in (-4096, 0, 4096):
            result = mix_components({'spring': 65534, 'constant': transient},
                                    condition_headroom=.25)
            self.assertEqual(result.limited, .75*32767 + transient)
            self.assertTrue(result.condition_limited)
            self.assertFalse(result.clipped)
        # Sem reserva, documenta o limite físico de uma soma acima do teto.
        self.assertEqual(mix_components({'spring': 65534, 'constant': -4096}).limited, 32767)

    def test_reserve_preserves_cancellation_inside_conditional_aggregate(self):
        result = mix_components({'spring': 40000, 'damper': -40000,
                                 'periodic': 8000}, condition_headroom=.25)
        self.assertEqual(result.limited, 8000)
        self.assertFalse(result.condition_limited)

    def test_mock_exposes_clipping_separately_from_condition_limiting(self):
        linear = ConstantOutput(strength_percent=20, clock=lambda: self.now)
        linear.tick({'spring': 65534})
        self.assertEqual(linear.saturation_ticks, 1)
        reserved = ConstantOutput(strength_percent=20, condition_headroom=.25,
                                  clock=lambda: self.now)
        reserved.tick({'spring': 65534})
        self.assertEqual(reserved.saturation_ticks, 0)
        self.assertEqual(reserved.condition_limited_ticks, 1)

    def test_mixer_rejects_invalid_values_and_overflow(self):
        for values in ({'constant': math.nan}, {'constant': math.inf},
                       {'constant': '100'}, {'unknown': 0},
                       {'constant': 1e308, 'spring': 1e308}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                mix_components(values)
        for kwargs in (dict(gain=-1), dict(gain=math.nan), dict(limit=-1),
                       dict(condition_headroom=2), dict(periodic_gain=math.inf)):
            with self.assertRaises(ValueError):
                mix_components({}, **kwargs)

    def test_invalid_force_stops_and_disarms_existing_mock_output(self):
        motor = ConstantOutput(clock=lambda: self.now)
        motor.tick(16000)
        self.assertTrue(motor.playing)
        with self.assertRaises(ValueError):
            motor.tick({'constant': math.nan})
        self.assertFalse(motor.playing)
        self.assertFalse(motor.enabled)

    def test_zero_limit_and_zero_gain_never_emit_force(self):
        self.assertEqual(mix_components({'constant': 32000}, gain=0).limited, 0)
        self.assertEqual(mix_components({'constant': -32000}, limit=0).limited, 0)


if __name__ == '__main__':
    unittest.main()
