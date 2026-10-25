"""Vetores sintéticos de intensidade; relógio falso, sem dispositivos reais."""
import contextlib
import io
import unittest
from unittest.mock import patch

from test_ffb_audit import ROOT
from dfgt_bridge import runner
from dfgt_bridge.backends.mock import MockBackend
from dfgt_bridge.ffb.effects import OUTPUT_COMPONENTS
from dfgt_bridge.ffb.safety import ConstantOutput
from dfgt_bridge.supervised import SupervisedPolicy


class IntensityTests(unittest.TestCase):
    def policy(self, strength=100, maximum=100, **kwargs):
        dev = MockBackend(clock=lambda: policy.now_ns/1e9)
        policy = SupervisedPolicy(dev, strength=strength, maximum=maximum, **kwargs)
        self.addCleanup(lambda: None if policy.closed else policy.apply('close', {}, policy.now_ns))
        return policy, dev

    def frame(self, policy, level=32767, dt=25_000_000):
        now = policy.now_ns+dt
        return policy.apply('frame', dict(generation=policy.last_generation+1, calculated_ns=now,
                            components={k: level if k == 'constant' else 0 for k in OUTPUT_COMPONENTS}), now)

    def test_reference_intensities_reach_expected_evdev_levels(self):
        for percent in (0, 10, 30, 50, 75, 100):
            with self.subTest(percent=percent):
                policy, dev = self.policy(percent)
                for _ in range(100):
                    result = self.frame(policy)
                self.assertEqual(result['evdev_level'], round(32767*percent/100))
                self.assertEqual(dev.level, round(32767*percent/100))
                self.assertEqual(policy.output.boost, 1)

    def test_full_scale_negative_and_polarity_are_preserved(self):
        for polarity, source, expected in ((1, -32768, -32767), (-1, 32767, -32767), (-1, -32768, 32767)):
            with self.subTest(polarity=polarity, source=source):
                policy, dev = self.policy(polarity=polarity)
                for _ in range(100):
                    self.frame(policy, source)
                self.assertEqual(dev.level, expected)

    def test_runtime_range_and_emergency_are_independent(self):
        policy, dev = self.policy(0)
        for percent in (10, 30, 50, 75, 100, 75, 50, 30, 10, 0):
            policy.apply('strength', {'percent': percent}, policy.now_ns+1)
            for _ in range(100):
                self.frame(policy)
            self.assertEqual(dev.level, round(32767*percent/100))
        policy.apply('strength', {'percent': 100}, policy.now_ns+1)
        for _ in range(100):
            self.frame(policy)
        policy.apply('stop', {'reason': 'F9', 'latch': True}, policy.now_ns+1)
        self.assertEqual(dev.level, 0)
        policy.apply('strength', {'percent': 100}, policy.now_ns+1)
        self.frame(policy)
        self.assertEqual(dev.level, 0)
        self.assertTrue(policy.latched)

    def test_zero_maximum_never_uploads(self):
        policy, dev = self.policy(0, 0)
        for _ in range(100):
            self.frame(policy)
        self.assertEqual(policy.output.writes, 0)
        self.assertEqual(dev.level, 0)

    def test_zero_and_disable_bypass_slew_and_rate_gate_at_full_scale(self):
        policy, dev = self.policy(slew_per_s=327670)
        for _ in range(6):
            self.frame(policy)
        self.assertEqual(dev.level, 32767)
        self.frame(policy, 0, dt=1)
        self.assertEqual(dev.level, 0)
        for _ in range(6):
            self.frame(policy)
        policy.apply('stop', {'reason': 'disable_actuators'}, policy.now_ns+1)
        self.assertEqual(dev.level, 0)

    def test_explicit_slew_reaches_full_scale_without_amplitude_boost(self):
        policy, dev = self.policy(slew_per_s=327670)
        levels = [self.frame(policy)['evdev_level'] for _ in range(6)]
        self.assertEqual(levels[-1], 32767)
        self.assertTrue(all(0 <= v <= 32767 for v in levels))
        self.assertLess(levels[0], 32767)

    def test_invalid_profiles_and_slew_fail_before_any_io(self):
        for strength, maximum in ((101, 100), (-1, 100), (10, 0), (0, -1), (0, 101), (True, 100), (0, 10.0)):
            dev = MockBackend()
            with self.subTest(strength=strength, maximum=maximum), self.assertRaises(ValueError):
                SupervisedPolicy(dev, strength=strength, maximum=maximum)
            self.assertEqual(dev.actions, [])
        for rate in (0, -1, float('nan'), float('inf')):
            with self.subTest(rate=rate), self.assertRaises(ValueError):
                self.policy(slew_per_s=rate)

    def test_invalid_ipc_strength_stops_and_erases(self):
        for percent in (-1, 101, 200, 0.5, True, '100'):
            policy, dev = self.policy()
            self.frame(policy)
            with self.subTest(percent=percent), self.assertRaises(ValueError):
                policy.apply('strength', {'percent': percent}, policy.now_ns+1)
            self.assertTrue(policy.closed)
            self.assertEqual(dev.level, 0)
            self.assertIsNone(dev.effect)

    def test_output_accepts_full_range_and_rejects_invalid_percentages(self):
        for percent in (0, 10, 30, 50, 75, 100):
            motor = ConstantOutput(MockBackend(), percent, max_strength_percent=percent)
            self.assertEqual(motor.cap, round(32767*percent/100))
            motor.close()
        for value in (-1, 101, True, 10.5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                ConstantOutput(MockBackend(), value, max_strength_percent=100)

    def test_cli_accepts_full_scale_and_preserves_passive_default(self):
        with patch.object(runner, 'run') as run, patch.object(runner.signal, 'signal'):
            self.assertEqual(runner.main(['--ffb-strength', '100', '--ffb-max-strength', '100',
                                         '--ffb-slew-rate', '327670']), 0)
        args = run.call_args.args[0]
        self.assertEqual((args.ffb_strength, args.ffb_max_strength, args.ffb_slew_rate), (100, 100, 327670))
        self.assertFalse(args.enable_ffb)

    def test_cli_rejects_invalid_ranges_and_strength_above_maximum(self):
        for args in (['--ffb-strength', '101'], ['--ffb-max-strength', '-1'],
                     ['--ffb-strength', '100', '--ffb-max-strength', '50'],
                     ['--ffb-slew-rate', 'nan'], ['--ffb-slew-rate', '0']):
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()), \
                    patch.object(runner, 'run') as run, self.assertRaises(SystemExit):
                runner.main(args)
            run.assert_not_called()
