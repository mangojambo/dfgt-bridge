"""Capturas sintéticas pareadas: mesmo PID/renderer e política física mock."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from test_ffb_audit import ROOT
from test_ffb_transport import output, set_report
from dfgt_bridge.backends.mock import MockBackend
from dfgt_bridge.capture import CaptureWriter, Recorder, implementation_hash, canonical, digest
from dfgt_bridge.passive import PassiveSession
from dfgt_bridge.physical_replay import replay_physical
from dfgt_bridge.supervised import SupervisedPolicy


class PhysicalReplayTests(unittest.TestCase):
    def setUp(self):
        self.directory = self.enterContext(tempfile.TemporaryDirectory())
        self.core_path = Path(self.directory) / 'core.jsonl'
        self.trace_path = Path(self.directory) / 'physical.jsonl'
        self.session = PassiveSession(0, gas_axis='y')
        self.addCleanup(self.session.core.close)
        self.core_writer = CaptureWriter(self.core_path, self.session.metadata())
        self.addCleanup(self.core_writer.close)
        self.recorder = Recorder(self.session, self.core_writer)
        self.config = dict(strength=10, maximum=20, polarity=-1, session_s=120, slew_per_s=16383.5)
        self.policy = SupervisedPolicy(MockBackend(clock=lambda: self.policy.now_ns/1e9), **self.config)
        self.trace_writer = CaptureWriter(self.trace_path, dict(kind='physical_policy', config=self.config,
                                          diagnostics_version=2,
                                          implementation_sha256=implementation_hash()))
        self.addCleanup(self.trace_writer.close)
        self.now = 1_000_000
        self.generation = 0

    def core(self, packet=None, feature=False):
        self.now += 30_000_000
        if packet:
            event = set_report(packet, self.now, 0) if feature else output(packet)
            result = self.recorder.dispatch('uhid', {'raw_hex': event.hex()}, self.now)
        else:
            result = self.recorder.dispatch('tick', {}, self.now)
        self.generation += 1
        self.physical('frame', dict(generation=self.generation, calculated_ns=self.now,
                                   components=result['components']), self.now+1_000_000)

    def physical(self, action, data, stamp):
        result = self.policy.apply(action, data, stamp)
        self.trace_writer.append(dict(kind='physical_step', action=action, data=data,
                                      timestamp_ns=stamp, result=result))

    def race(self):
        self.core(bytes.fromhex('54010000'), True)
        self.core(bytes.fromhex('5501803e'))
        self.core(bytes.fromhex('5a010101'))
        for _ in range(10):
            self.core()
        self.physical('stop', dict(reason='F9', latch=True), self.now+2_000_000)
        self.core()
        self.physical('arm', dict(requested_ns=self.now+3_000_000), self.now+3_000_000)
        self.core()
        self.core(bytes.fromhex('5002'))  # Disable: renderer soma zero.
        self.core()
        self.recorder.dispatch('close', {}, self.now+4_000_000)
        self.core_writer.append(dict(kind='footer', complete=True, summary=self.session.summary()))
        self.physical('close', {}, self.now+5_000_000)
        self.trace_writer.append(dict(kind='footer', complete=True, error=None, result=self.policy.snapshot()))
        self.core_writer.flush()
        self.trace_writer.flush()

    def rewrite(self, path, rows):
        previous = '0'*64
        encoded = []
        for i, row in enumerate(rows):
            row = dict(row, seq=i, previous=previous)
            row.pop('sha256', None)
            row['sha256'] = previous = digest(row)
            encoded.append(canonical(row))
        path.write_text('\n'.join(encoded)+'\n')

    def test_full_core_and_physical_policy_replay_match_without_any_hardware(self):
        self.race()
        result = replay_physical(self.trace_path, core_capture=self.core_path)
        self.assertTrue(result['complete'])
        self.assertTrue(result['core_correlated'])
        self.assertGreater(result['frames'], 10)
        self.assertFalse(result['physical_hardware_validated'])
        self.assertEqual(result['summary']['evdev_level'], 0)
        rows = [json.loads(line) for line in self.trace_path.read_text().splitlines()]
        # 16000 do renderer × 10%, sem multiplicar também pelos 20% do mock principal.
        self.assertEqual(max(abs(row['result']['limited_force']) for row in rows
                             if row['kind'] == 'physical_step'), 16000*3277/32767)
        self.assertEqual(max(abs(row['result']['evdev_level']) for row in rows
                             if row['kind'] == 'physical_step'), 1600)

    def test_physical_trace_force_tampering_is_detected_even_with_valid_hashes(self):
        self.race()
        rows = [json.loads(line) for line in self.trace_path.read_text().splitlines()]
        rows[3]['result']['evdev_level'] += 1
        self.rewrite(self.trace_path, rows)
        with self.assertRaisesRegex(ValueError, 'divergiu'):
            replay_physical(self.trace_path)

    def test_core_frame_mismatch_is_not_hidden_by_valid_physical_trace(self):
        self.race()
        other = Path(self.directory) / 'other.jsonl'
        # Outra captura válida, sem essas amostras, não pode ser pareada.
        different = PassiveSession(0)
        with_writer = CaptureWriter(other, different.metadata())
        try:
            recorder = Recorder(different, with_writer)
            recorder.dispatch('close', {}, 1)
            with_writer.append(dict(kind='footer', complete=True, summary=different.summary()))
        finally:
            with_writer.close()
        with self.assertRaisesRegex(ValueError, 'não corresponde'):
            replay_physical(self.trace_path, core_capture=other)

    def test_missing_footer_is_incomplete_not_success(self):
        self.race()
        rows = [json.loads(line) for line in self.trace_path.read_text().splitlines()]
        self.rewrite(self.trace_path, rows[:-1])
        self.assertFalse(replay_physical(self.trace_path)['complete'])

    def test_omitted_or_duplicated_physical_step_is_rejected(self):
        self.race()
        original = self.trace_path.read_text().splitlines(keepends=True)
        for rows in (original[:2]+original[3:], original[:2]+original[1:]):
            self.trace_path.write_text(''.join(rows))
            with self.assertRaises(ValueError):
                replay_physical(self.trace_path)

    def test_incompatible_sources_require_explicit_full_semantic_verification(self):
        self.race()
        rows = [json.loads(line) for line in self.trace_path.read_text().splitlines()]
        rows[0]['metadata']['implementation_sha256'] = '0'*64
        self.rewrite(self.trace_path, rows)
        with self.assertRaises(ValueError):
            replay_physical(self.trace_path)
        self.assertTrue(replay_physical(self.trace_path, verify_compatible=True)['complete'])

    def test_legacy_trace_without_added_diagnostics_still_verifies_all_old_fields(self):
        self.race()
        rows = [json.loads(line) for line in self.trace_path.read_text().splitlines()]
        rows[0]['metadata'].pop('diagnostics_version')
        for row in rows[1:]:
            row['result'].pop('force_pipeline')
        self.rewrite(self.trace_path, rows)
        self.assertTrue(replay_physical(self.trace_path)['complete'])
        rows[3]['result']['evdev_level'] += 1
        self.rewrite(self.trace_path, rows)
        with self.assertRaisesRegex(ValueError, 'divergiu'):
            replay_physical(self.trace_path)

    def test_v2_diagnostics_cannot_be_omitted_or_tampered_to_hide_attenuation(self):
        self.race()
        original = [json.loads(line) for line in self.trace_path.read_text().splitlines()]
        for operation in ('omit', 'tamper'):
            rows = copy.deepcopy(original)
            if operation == 'omit':
                rows[3]['result'].pop('force_pipeline')
            else:
                rows[3]['result']['force_pipeline']['user_gain'] = 1
            self.rewrite(self.trace_path, rows)
            with self.subTest(operation=operation), self.assertRaisesRegex(ValueError, 'divergiu'):
                replay_physical(self.trace_path)

    def test_signed_evdev_telemetry_is_validated_and_correlated_with_output(self):
        self.race()
        original = [json.loads(line) for line in self.trace_path.read_text().splitlines()]
        for row in original[1:-1]:
            row['io'] = dict(gain_commanded=row['result']['force_pipeline']['evdev_gain_expected'],
                             level_commanded=row['result']['evdev_level'], level_uploaded=-32768,
                             io_calls=100, last_io_ns=1000, max_io_ns=1000)
        self.rewrite(self.trace_path, original)
        self.assertTrue(replay_physical(self.trace_path)['complete'])
        rows = copy.deepcopy(original)
        rows[3]['io']['level_commanded'] += 1
        self.rewrite(self.trace_path, rows)
        with self.assertRaisesRegex(ValueError, 'Magnitude EVDEV'):
            replay_physical(self.trace_path)
        rows = copy.deepcopy(original)
        rows[3]['io']['gain_commanded'] = 65536
        self.rewrite(self.trace_path, rows)
        with self.assertRaisesRegex(ValueError, 'Telemetria EVDEV'):
            replay_physical(self.trace_path)
