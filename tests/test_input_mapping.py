"""Mapeamento de entrada e replay, vetores sintéticos; sem dispositivos."""
from contextlib import closing
from pathlib import Path
import json
import tempfile
import unittest
from test_ffb_audit import ROOT
from dfgt_bridge.input.mapping import preset, map_state, warnings, validate_mapping
from dfgt_bridge.input.virtual_g29 import report
from dfgt_bridge.passive import PassiveSession
from dfgt_bridge.capture import CaptureWriter, Recorder
from dfgt_bridge.replay import replay


def state(*keys, hx=0, hy=0, x=8192, gas=255, brake=255):
    return (x, gas, brake, hx, hy, list(keys))


class MappingTests(unittest.TestCase):
    def test_native_paddles_and_stick_use_l1_r1_without_face_buttons(self):
        mapping = preset()
        for code, target in ((293, 6), (301, 6), (292, 5), (300, 5)):
            with self.subTest(code=code):
                packet = report(map_state(state(code), mapping), 'y')
                self.assertEqual(int.from_bytes(packet[7:11], 'little'), 1 << (target-1))
                self.assertEqual(packet[6], 8)

    def test_aggregate_release_does_not_release_other_held_source(self):
        mapping = preset()
        packets = [report(map_state(state(*keys), mapping)) for keys in ((293, 301), (301,), (293,), ())]
        self.assertEqual([int.from_bytes(p[7:11], 'little') for p in packets], [32, 32, 32, 0])

    def test_native_face_shoulders_and_menus_remain_byte_identical(self):
        for code in range(288, 300):
            with self.subTest(code=code):
                self.assertEqual(report(map_state(state(code), preset()), 'y'), report(state(code), 'y'))

    def test_unidentified_extra_legacy_codes_preserve_their_bits(self):
        for code in (709, 710, 711, 712):
            self.assertEqual(report(map_state(state(code), preset()), 'y'), report(state(code), 'y'))

    def test_hat_and_plus_minus_aggregate_without_sticking(self):
        mapping = preset()
        for physical, expected in ((state(303), 0), (state(706), 4),
                                    (state(303, hx=1), 1), (state(303, 706), 8),
                                    (state(303, hy=-1), 0), (state(hx=-1, hy=1), 5)):
            with self.subTest(physical=physical):
                packet = report(map_state(physical, mapping))
                self.assertEqual(packet[6], expected)
                self.assertEqual(int.from_bytes(packet[7:11], 'little'), 0)

    def test_horn_enter_rotary_and_guide_use_separate_virtual_slots(self):
        for physical, button in ((707, 20), (302, 24), (704, 22), (705, 23), (708, 25)):
            with self.subTest(physical=physical):
                packet = report(map_state(state(physical), preset()))
                self.assertEqual(int.from_bytes(packet[7:11], 'little'), 1 << (button-1))

    def test_default_axes_preserve_throttle_brake_and_steering_ranges(self):
        for x, gas, brake in ((0, 255, 255), (8192, 128, 12), (16383, 0, 0)):
            original = state(x=x, gas=gas, brake=brake)
            self.assertEqual(report(map_state(original, preset()), 'y'), report(original, 'y'))

    def test_custom_axis_normalization_and_inversion(self):
        mapping = preset()
        mapping['axes']['accelerator'] = dict(source=2, invert=True)
        mapping['axes']['steering'] = dict(source=1, invert=False)
        mapped = map_state(state(gas=255, brake=0), mapping)
        self.assertEqual(mapped[:3], (16383, 255, 0))

    def test_gears_preset_is_explicit_and_destinations_configurable(self):
        standard = preset()
        gears = preset('gears')
        self.assertEqual(standard['bindings']['key:293'], 'button:6')
        self.assertEqual(gears['bindings']['key:293'], 'button:2')
        custom = preset('gears', down='button:13', up='button:14')
        self.assertEqual(custom['bindings']['key:301'], 'button:13')
        self.assertEqual(custom['bindings']['key:300'], 'button:14')

    def test_shared_codes_are_not_falsely_distinguished(self):
        notes = '\n'.join(warnings(preset()))
        self.assertIn('300/301', notes)
        self.assertIn('não são distinguíveis', notes)
        self.assertIn('confirm', notes)
        self.assertEqual(len([s for s in preset()['bindings'] if s == 'key:301']), 1)

    def test_invalid_mapping_is_rejected_without_mutation(self):
        for source, target in (('key:768', 'button:1'), ('key:-1', 'button:1'),
                               ('key:0293', 'button:1'), ('key:293', 'button:26'),
                               ('abs:0', 'button:1')):
            mapping = preset()
            mapping['bindings'][source] = target
            with self.subTest(source=source, target=target), self.assertRaises(ValueError):
                validate_mapping(mapping)

    def test_capture_replay_uses_recorded_mapping_and_preserves_raw_input(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'mapped.jsonl'
            session = PassiveSession(0, gas_axis='y', mapping=preset())
            with closing(CaptureWriter(path, session.metadata())) as writer:
                recorder = Recorder(session, writer)
                result = recorder.dispatch('input', {'state': state(301, 707, gas=128)}, 1_000_000)
                self.assertEqual(int.from_bytes(bytes.fromhex(result['info']['input_report_hex'])[7:11], 'little'), (1 << 5) | (1 << 19))
                recorder.dispatch('close', {}, 2_000_000)
                writer.append(dict(kind='footer', complete=True, summary=session.summary()))
            self.assertTrue(replay(path)['complete'])
            rows = [json.loads(s) for s in path.read_text().splitlines()]
            self.assertEqual(rows[1]['data']['state'][5], [301, 707])

