"""Synthetic PID calibration vectors; no physical devices or reference scripts."""
import contextlib
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from test_ffb_audit import ROOT
from dfgt_bridge import runner
from dfgt_bridge.backends.mock import MockBackend
from dfgt_bridge.config import ProfileStore, default_profile, validate_profile
from dfgt_bridge.ffb.effects import PIDSteeringBridge, OUTPUT_COMPONENTS
from dfgt_bridge.ffb.renderer import MotionState, signed_unit, unsigned_unit
from dfgt_bridge.ffb.mixer import calibrate_components, mix_components, validate_effect_gains
from dfgt_bridge.mapping_editor import MappingEditor
from dfgt_bridge.steam_protocol import approved
from dfgt_bridge.supervised import SupervisedPolicy


class CalibrationTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.engine = PIDSteeringBridge(clock=lambda: self.now/1e9)
        self.engine.arm()

    def spring(self, coefficient=32767, saturation=65535, center=0, deadband=0, direction=9000, flags=1):
        self.engine.set_report(bytes.fromhex('54090000'), 0x54)
        block = self.engine.last_block
        self.engine.write_output(bytes((0x51, block, 9)) + struct.pack('<HHHHBBBHH',
            0xffff, 0, 0, 0, 100, 255, flags, direction, 0))
        self.engine.write_output(bytes((0x53, block, 0)) + struct.pack('<hhhHHH',
            center, coefficient, coefficient, saturation, saturation, deadband))
        self.engine.write_output(bytes((0x5a, block, 1, 1)))
        return block

    def policy(self, **options):
        backend = MockBackend(clock=lambda: self.now/1e9)
        policy = SupervisedPolicy(backend, **options)
        self.addCleanup(lambda: policy.apply('close', {}, self.now) if not policy.closed else None)
        return policy

    def send(self, policy, components):
        self.now += 25_000_000
        return policy.apply('frame', dict(generation=policy.last_generation+1,
            calculated_ns=self.now, components=components), self.now)

    def settle(self, policy, components):
        for _ in range(10):
            value = self.send(policy, components)
        return value

    def test_spring_positions_both_sides_restore_and_use_more_command_range(self):
        self.spring(coefficient=8192)
        baseline = self.policy(effect_gains={'spring': 1})
        calibrated = self.policy(effect_gains={'spring': 2})
        for position in (-1, -.5, -.25, -.1, .1, .25, .5, 1):
            with self.subTest(position=position):
                components = self.engine.components(motion=MotionState(position))
                expected = -8192*position
                self.assertAlmostEqual(components['spring'], expected)
                self.assertLess(components['spring']*position, 0)
                # Both supervisors must advance while sharing this clock.
                for _ in range(10):
                    before = self.send(baseline, components)
                    after = self.send(calibrated, components)
                self.assertEqual(before['evdev_level'], round(-expected))
                self.assertEqual(after['evdev_level'], round(-2*expected))
                self.assertGreater(abs(after['evdev_level']), abs(before['evdev_level']))
                # Approved polarity -1: physical command sign is opposite to
                # renderer force. Mechanical direction still needs owner validation.
                self.assertGreater(after['evdev_level']*position, 0)
                self.assertEqual(after['effect_calibration']['total'], components['spring']*2)

    def test_development_default_uses_spring_three_and_other_gains_one(self):
        policy = self.policy()
        raw = dict.fromkeys(OUTPUT_COMPONENTS, 0)
        raw.update(spring=-1000, constant=100)
        value = self.settle(policy, raw)
        self.assertEqual(value['effect_calibration']['gains'],
                         {family: 3.0 if family == 'spring' else 1.0 for family in OUTPUT_COMPONENTS})
        self.assertEqual(value['evdev_level'], 2900)
        self.assertEqual((value['strength'], value['maximum'], value['polarity']), (100, 100, -1))
        self.assertEqual(policy.output.slew_per_s, 327670)
        self.assertIsNone(policy.output.test_limit_s)

    def test_full_coefficient_reaches_physical_saturation_without_family_clipping(self):
        self.spring()
        policy = self.policy(effect_gains={'spring': 2})
        for position in (-1, -.5, -.25, -.1, .1, .25, .5, 1):
            components = self.engine.components(motion=MotionState(position))
            value = self.settle(policy, components)
            expected = max(-32767, min(32767, 2*32767*position))
            self.assertEqual(value['evdev_level'], round(expected))
            self.assertEqual(value['mix']['clipped'], abs(position) > .5)
            self.assertAlmostEqual(value['effect_calibration']['total'], -2*32767*position)

    def test_center_deadband_and_unsigned_saturation_remain_in_pid_coordinates(self):
        self.spring(center=8192, deadband=6554, saturation=16384)
        center, band = signed_unit(8192), unsigned_unit(6554)
        policy = self.policy(effect_gains={'spring': 2})
        for position in (center, center-band/2, center+band/2):
            raw = self.engine.components(motion=MotionState(position))
            self.assertEqual(raw['spring'], 0)
            self.assertEqual(self.settle(policy, raw)['evdev_level'], 0)
        for position, sign in ((-1, 1), (1, -1)):
            raw = self.engine.components(motion=MotionState(position))
            expected = sign*16384/65535*32767
            self.assertAlmostEqual(raw['spring'], expected)
            value = self.settle(policy, raw)
            self.assertEqual(value['evdev_level'], round(-2*expected))

    def test_positive_calibration_preserves_signed_coefficients_and_polar_direction(self):
        for coefficient in (32767, -32768):
            for direction in (9000, 27000):
                self.engine.effects.clear()
                self.spring(coefficient=coefficient, flags=4, direction=direction)
                raw = self.engine.components(motion=MotionState(.25))
                scaled = calibrate_components(raw, {'spring': 3})
                self.assertEqual(scaled['spring'], raw['spring']*3)
                if coefficient > 0:
                    self.assertLess(raw['spring'], 0)
                else:
                    self.assertGreater(raw['spring'], 0)

    def test_individual_gains_cover_all_seven_families_and_both_signs(self):
        for family in OUTPUT_COMPONENTS:
            for sign in (-1, 1):
                with self.subTest(family=family, sign=sign):
                    policy = self.policy(effect_gains={family: 2})
                    raw = dict.fromkeys(OUTPUT_COMPONENTS, 0)
                    raw[family] = sign*1000
                    value = self.settle(policy, raw)
                    self.assertEqual(value['renderer_components'], raw)
                    self.assertEqual(value['evdev_level'], -sign*2000)
                    self.assertEqual(value['force_pipeline']['after_user_gain'], -sign*2000)

    def test_gain_mixes_before_final_clipping_and_preserves_cancellation(self):
        raw = dict.fromkeys(OUTPUT_COMPONENTS, 0)
        raw.update(spring=-40000, constant=40000)
        self.assertEqual(mix_components(calibrate_components(raw)).limited, 0)
        scaled = calibrate_components(raw, {'spring': 2})
        mixed = mix_components(scaled)
        self.assertEqual(mixed.unlimited_requested, -40000)
        self.assertEqual(mixed.limited, -32767)
        self.assertTrue(mixed.clipped)
        raw.update(spring=-4000, constant=1000)
        self.assertEqual(mix_components(calibrate_components(raw, {'spring': 2})).limited, -7000)

    def test_operator_snapshot_components_have_quantifiable_gain_without_constant_boost(self):
        # Static vector derived from the operator's F10, not a race time series.
        raw = dict.fromkeys(OUTPUT_COMPONENTS, 0)
        raw.update(spring=-2521.08, constant=-1)
        policy = self.policy(effect_gains={'spring': 2})
        value = self.settle(policy, raw)
        self.assertEqual(value['renderer_total'], -2522.08)
        self.assertEqual(value['effect_calibration']['total'], -5043.16)
        self.assertEqual(value['effect_calibration']['components']['constant'], -1)
        self.assertEqual(value['evdev_level'], 5043)
        self.assertFalse(value['mix']['clipped'])

    def test_stop_pause_disable_free_reset_never_synthesize_autocenter(self):
        block = self.spring()
        policy = self.policy(effect_gains={'spring': 8})
        for stop, restart in ((bytes((0x5a, block, 3, 0)), bytes((0x5a, block, 1, 1))),
                              (bytes.fromhex('5005'), bytes.fromhex('5006')),
                              (bytes.fromhex('5002'), bytes.fromhex('5001'))):
            active = self.engine.components(motion=MotionState(.5))
            self.assertNotEqual(self.settle(policy, active)['evdev_level'], 0)
            self.engine.write_output(stop)
            raw = self.engine.components(motion=MotionState(.5))
            self.assertEqual(raw, dict.fromkeys(OUTPUT_COMPONENTS, 0))
            self.assertEqual(self.send(policy, raw)['evdev_level'], 0)
            self.engine.write_output(restart)
        self.engine.write_output(bytes((0x5b, block)))
        self.assertEqual(self.send(policy, self.engine.components(motion=MotionState(.5)))['evdev_level'], 0)
        self.engine.write_output(bytes.fromhex('5004'))
        self.assertEqual(self.send(policy, self.engine.components(motion=MotionState(-1)))['evdev_level'], 0)

    def test_zero_gain_cannot_create_force_and_invalid_gain_is_rejected_before_output(self):
        raw = dict.fromkeys(OUTPUT_COMPONENTS, 0)
        raw['spring'] = -32767
        self.assertEqual(self.settle(self.policy(effect_gains={'spring': 0}), raw)['evdev_level'], 0)
        for gains in ([], {'unknown': 2}, {'spring': True}, {'spring': -1},
                      {'spring': 9}, {'spring': float('nan')}, {'spring': float('inf')}):
            with self.subTest(gains=gains), self.assertRaises(ValueError):
                validate_effect_gains(gains)

    def test_invalid_or_overflowing_components_stop_active_output(self):
        for bad, raises in ((float('nan'), False), (1e308, True)):
            policy = self.policy(effect_gains={'spring': 8})
            raw = dict.fromkeys(OUTPUT_COMPONENTS, 0)
            raw['spring'] = -1000
            self.settle(policy, raw)
            plays = policy.output.play_requests
            raw['spring'] = bad
            if raises:  # Overflow is terminal, invalid wire numeric is a latch.
                with self.assertRaises(ValueError):
                    self.send(policy, raw)
            else:
                self.send(policy, raw)
            self.assertTrue(policy.latched)
            self.assertEqual(policy.output.last_sent, 0)
            self.assertEqual(policy.output.play_requests, plays)


class CalibrationProfileTests(unittest.TestCase):
    def setUp(self):
        self.directory = self.enterContext(tempfile.TemporaryDirectory())
        self.store = ProfileStore(Path(self.directory)/'profiles.json')

    def test_old_profile_migrates_to_spring_three_and_editor_preserves_explicit_calibration(self):
        old = default_profile()
        old.pop('ffb_effect_gains')
        old.pop('ffb_spring_gain')
        self.assertEqual(validate_profile(old)['ffb_spring_gain'], 3.0)
        self.assertEqual(validate_profile(old)['ffb_effect_gains'], {})
        profile = default_profile()
        profile.pop('ffb_spring_gain')
        profile['ffb_effect_gains'] = {'spring': 2, 'damper': 1.25}
        self.store.save('dirt5', profile)
        editor = MappingEditor(self.store.load())
        editor.apply_preset('g29')
        self.assertEqual(editor.profile['ffb_spring_gain'], 2.0)  # Explicit legacy custom value.
        self.assertEqual(editor.profile['ffb_effect_gains'], {'damper': 1.25})
        self.assertEqual(approved(editor.profile), validate_profile(profile))

    def test_cli_overrides_and_saves_same_profile_without_implicit_physical_opt_in(self):
        argv = ['--profiles-file', str(self.store.path), '--save-profile']
        for family in OUTPUT_COMPONENTS:
            argv += [f'--ffb-{family}-gain', '2' if family == 'spring' else '1']
        with patch.object(runner, 'run') as run, patch.object(runner.signal, 'signal'):
            self.assertEqual(runner.main(argv), 0)
        options = run.call_args.args[0]
        self.assertEqual(options.ffb_effect_gains, self.store.load()['ffb_effect_gains'])
        self.assertEqual(options.ffb_spring_gain, 2.0)  # Explicit CLI override.
        self.assertEqual(options.ffb_strength, 100)
        self.assertEqual(options.ffb_polarity, -1)
        self.assertFalse(options.enable_ffb)
        self.assertIsNone(options.capture)

    def test_invalid_calibration_cannot_replace_valid_profile(self):
        self.store.save('dirt5', default_profile())
        before = self.store.path.read_bytes()
        with patch.object(runner, 'run') as run, contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            runner.main(['--profiles-file', str(self.store.path), '--save-profile', '--ffb-spring-gain', 'nan'])
        run.assert_not_called()
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_spring_field_is_persisted_and_explicit_unity_survives_round_trip(self):
        self.store.save('dirt5', default_profile())
        self.assertNotIn('ffb_effect_gains', json.loads(self.store.path.read_text())['profiles']['dirt5'])
        self.assertEqual(json.loads(self.store.path.read_text())['profiles']['dirt5']['ffb_spring_gain'], 3.0)
        profile = self.store.load()
        profile['ffb_spring_gain'] = 1.0
        self.store.save('dirt5', profile)
        self.assertEqual(json.loads(self.store.path.read_text())['profiles']['dirt5']['ffb_spring_gain'], 1.0)
        profile['ffb_effect_gains'] = {}
        self.store.save('dirt5', profile)
        self.assertNotIn('ffb_effect_gains', json.loads(self.store.path.read_text())['profiles']['dirt5'])
        self.assertEqual(self.store.load()['ffb_spring_gain'], 1.0)

    def test_legacy_and_new_custom_spring_values_survive_validation_and_launcher_approval(self):
        for gain in (0, 1, 1.25, 3, 4):
            for legacy in (True, False):
                with self.subTest(gain=gain, legacy=legacy):
                    profile = default_profile()
                    profile['ffb_effect_gains'] = {'damper': 1.5, 'periodic': .75}
                    if legacy:
                        profile.pop('ffb_spring_gain')
                        profile['ffb_effect_gains']['spring'] = gain
                    else:
                        profile['ffb_spring_gain'] = gain
                    value = approved(profile)
                    self.assertEqual(value['ffb_spring_gain'], gain)
                    self.assertEqual(value['ffb_effect_gains'], {'damper': 1.5, 'periodic': .75})
                    self.store.save('dirt5', profile)
                    self.assertEqual(self.store.load(), value)

    def test_conflicting_or_invalid_flat_spring_fields_are_rejected(self):
        for bad in (True, -1, 9, float('nan'), float('inf')):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                approved(dict(default_profile(), ffb_spring_gain=bad))
        profile = default_profile()
        profile['ffb_effect_gains'] = {'spring': 1}
        with self.assertRaisesRegex(ValueError, 'conflitantes'):
            validate_profile(profile)

    def test_cli_can_override_migrated_legacy_custom_gain_without_extra_steam_arguments(self):
        profile = default_profile()
        profile.pop('ffb_spring_gain')
        profile['ffb_effect_gains'] = {'spring': 1.25}
        self.store.save('dirt5', profile)
        with patch.object(runner, 'run') as run, patch.object(runner.signal, 'signal'):
            runner.main(['--profiles-file', str(self.store.path)])
            self.assertEqual(run.call_args.args[0].ffb_spring_gain, 1.25)
            runner.main(['--profiles-file', str(self.store.path), '--ffb-spring-gain', '3'])
            self.assertEqual(run.call_args.args[0].ffb_spring_gain, 3)
        self.assertEqual(self.store.load()['ffb_spring_gain'], 1.25)
