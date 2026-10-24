"""Classifica os 16 rejeitados de cada captura sem executar protocolo Logitech."""
from collections import Counter
import json
import unittest

from test_ffb_audit import ROOT
from test_ffb_transport import output, set_report
from dfgt_bridge.diagnostics import classify_uhid_result
from dfgt_bridge.passive import PassiveSession


class ReportClassificationTests(unittest.TestCase):
    def setUp(self):
        self.session = PassiveSession(0)
        self.addCleanup(self.session.core.close)
        self.now = 0

    def send(self, raw):
        self.now += 1
        return self.session.apply('uhid', {'raw_hex': raw.hex()}, self.now)

    def test_all_16_rejected_packets_per_capture_are_known_legacy_commands(self):
        fixture = json.loads((ROOT / 'tests/fixtures/dirt5_passive_regression.json').read_text())
        commands = {}
        for sample in fixture['rejected']:
            result = self.send(output(bytes.fromhex(sample['packet_hex']), sample['report_type']))
            classification = classify_uhid_result(result)
            self.assertEqual(classification['category'], 'proprietary_ignored')
            self.assertEqual(result['error'], 'unsupported_report')  # Não mascarar o erro bruto.
            self.assertEqual(result['uploads'], 0)
            self.assertEqual(result['mock_actions'], [])
            commands.setdefault(sample['capture'], Counter())[classification['command']] += 1
        self.assertEqual(len(commands), 2)
        for counts in commands.values():
            self.assertEqual(counts, dict(autocenter_off=5, fixed_loop_off=10, leds_off=1))
        self.assertEqual(self.session.core.engine.effects, {})

    def test_unknown_commands_are_still_unknown_even_with_familiar_prefix(self):
        for packet in ('f8010000000000', 'f5000100000000', '0d020000000000', '10000000000000'):
            value = classify_uhid_result(self.send(output(bytes.fromhex(packet))))
            self.assertEqual(value['category'], 'unknown_report')
            self.assertIsNone(value['command'])

    def test_malformed_legacy_candidate_is_not_whitelisted(self):
        for packet in (bytes.fromhex('f500'), bytes.fromhex('f812000000000000')):
            self.assertEqual(classify_uhid_result(self.send(output(packet)))['category'], 'malformed_report')

    def test_malformed_pid_is_distinct_from_missing_block(self):
        malformed = self.send(output(b'\x55'))
        self.assertEqual(classify_uhid_result(malformed)['category'], 'malformed_report')
        absent = self.send(output(bytes.fromhex('55010000')))
        self.assertEqual(classify_uhid_result(absent)['category'], 'effect_state_error')

    def test_valid_pid_sent_on_wrong_report_type_is_not_logitech(self):
        result = self.send(set_report(bytes.fromhex('5900'), 42, 0))
        self.assertEqual(classify_uhid_result(result)['category'], 'pid_rejected')
        self.assertEqual(result['error'], 'report_type_not_writable')

    def test_classification_is_observational_and_does_not_mutate_effects(self):
        self.send(set_report(bytes.fromhex('54010000'), 1, 0))
        self.send(output(bytes.fromhex('5501803e')))
        running = self.send(output(bytes.fromhex('5a010101')))
        self.assertEqual(classify_uhid_result(running)['category'], 'pid')
        before = self.session.core.engine.effects[1].copy()
        for packet in ('f5000000000000', '0d000000000000', 'f8120000000000'):
            result = self.send(output(bytes.fromhex(packet)))
            self.assertEqual(result['uploads'], 0)
            self.assertEqual(classify_uhid_result(result)['category'], 'proprietary_ignored')
        self.assertEqual(self.session.core.engine.effects[1], before)

    def test_structural_transport_error_is_not_unknown_payload(self):
        result = self.send(b'\x01')
        self.assertEqual(classify_uhid_result(result)['category'], 'malformed_report')
