"""Pedais: configuração funcional v0.8, bytes dourados e amostras reais."""
import json
import struct
import unittest
from unittest.mock import patch

from test_ffb_audit import BASE, ROOT, load_reviewed_definitions
from dfgt_bridge import runner
from dfgt_bridge.input.physical_wheel import INPUT_EVENT, PhysicalWheel
from dfgt_bridge.input.virtual_g29 import report
from dfgt_bridge.passive import PassiveSession
import test_passive_input as input_tests


class ThrottleRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.baseline = load_reviewed_definitions(BASE)

    def check_state(self, state, expected_hex):
        session = PassiveSession(0, gas_axis='y')
        self.addCleanup(session.core.close)
        result = session.apply('input', {'state': state}, 1)
        expected = bytes.fromhex(expected_hex)
        self.assertEqual(bytes.fromhex(result['info']['input_report_hex']), expected)
        self.assertEqual(self.baseline.report(state, 'y', False), expected)
        virtual = bytes.fromhex(result['writes_hex'][0])
        self.assertEqual(struct.unpack_from('=IH', virtual), (12, 11))
        self.assertEqual(virtual[6:17], expected)

    def test_throttle_released(self):
        self.check_state([8192, 255, 255, 0, 0, []], '010280ffffff0800000000')

    def test_throttle_half_pressed(self):
        self.check_state([8192, 128, 255, 0, 0, []], '010280ffff800800000000')

    def test_throttle_fully_pressed(self):
        self.check_state([8192, 0, 255, 0, 0, []], '010280ffff000800000000')

    def test_brake_only(self):
        self.check_state([8192, 255, 0, 0, 0, []], '010280ff00ff0800000000')

    def test_pedals_simultaneously(self):
        self.check_state([8192, 0, 128, 0, 0, []], '010280ff80000800000000')

    def test_steering_endpoints_and_center_do_not_move_pedal_fields(self):
        for x, encoded in ((0, '0000'), (8192, '0280'), (16383, 'ffff')):
            with self.subTest(x=x):
                self.check_state([x, 128, 0, 0, 0, []], '01'+encoded+'ff00800800000000')

    def test_real_samples_match_working_v08_y_and_explain_captured_z_difference(self):
        fixture = json.loads((ROOT / 'tests/fixtures/dirt5_passive_regression.json').read_text())
        self.assertEqual(len(fixture['input_vectors']), 13)
        for vector in fixture['input_vectors']:
            with self.subTest(capture=vector['capture'], seq=vector['seq']):
                state = vector['state']
                self.assertEqual(report(state, 'z').hex(), vector['captured_z_hex'])
                self.check_state(state, vector['expected_v08_y_hex'])
                self.assertEqual(bytes.fromhex(vector['expected_v08_y_hex'])[5], state[1])

    def test_explicit_alternatives_and_inversion_still_match_baseline(self):
        state = [8192, 128, 64, 1, -1, [288, 704]]
        for axis in ('z', 'y', 'both'):
            for invert in (False, True):
                self.assertEqual(report(state, axis, invert), self.baseline.report(state, axis, invert))
        self.assertEqual(report(state, 'y', True)[5], 127)
        self.assertEqual(report(state, 'y', True)[4], 64)  # Freio não é invertido.

    def test_runner_defaults_to_y_and_preserves_explicit_axis_options(self):
        for argv, expected in (([], 'y'), (['--gas-axis', 'y'], 'y'),
                               (['--gas-axis', 'z'], 'z'), (['--gas-axis', 'both'], 'both')):
            with patch.object(runner, 'run') as run, patch.object(runner.signal, 'signal'):
                self.assertEqual(runner.main(argv), 0)
                self.assertEqual(run.call_args.args[0].gas_axis, expected)
                self.assertFalse(run.call_args.args[0].invert_gas)

    def test_native_input_events_reach_y_rz_and_x_without_swapping_or_rescaling(self):
        ioctl = input_tests.PhysicalInputTests().ioctl
        with patch('os.open', return_value=71), patch('os.close'), patch('fcntl.ioctl', side_effect=ioctl):
            with PhysicalWheel() as wheel:
                raw = b''.join(INPUT_EVENT.pack(0, 1000, kind, code, value) for kind, code, value in (
                    (3, 0, 16383), (3, 1, 128), (3, 2, 0), (0, 0, 0)))
                with patch('os.read', return_value=raw):
                    frames = wheel.read_frames()
                self.assertEqual(frames, [(16383, 128, 0, 0, 0, [])])
                self.check_state(frames[0], '01ffffff00800800000000')
