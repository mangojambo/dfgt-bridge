"""Vetores sintéticos e replay sem dispositivos, incluindo corrupção semântica."""
import copy
import gzip
import itertools
import json
from pathlib import Path
import struct
import tempfile
import unittest

from test_ffb_audit import ROOT
from test_ffb_transport import get_report, output, set_report
from dfgt_bridge.capture import CaptureWriter, Recorder, canonical, digest, records
from dfgt_bridge.passive import PassiveSession, describe_uhid
from dfgt_bridge.replay import replay

ORIGIN = 123_456_789_000_000


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.directory = self.enterContext(tempfile.TemporaryDirectory())
        self.path = Path(self.directory) / 'capture.jsonl'
        self.session = PassiveSession(ORIGIN)
        self.addCleanup(self.session.core.close)
        metadata = dict(self.session.metadata(), provenance='SYNTHETIC, not a DiRT 5 recording')
        self.writer = CaptureWriter(self.path, metadata)
        self.addCleanup(self.writer.close)
        self.recorder = Recorder(self.session, self.writer)
        self.request = itertools.count(1)
        self.ns = ORIGIN

    def step(self, kind, data=None, *, ms=None):
        self.ns = self.ns + 1_000_000 if ms is None else ORIGIN + round(ms*1e6)
        return self.recorder.dispatch(kind, data or {}, self.ns)

    def pid(self, packet, *, feature=False, via_output=False, request_id=None):
        event = output(packet, int(not feature)) if via_output else set_report(
            packet, next(self.request) if request_id is None else request_id, int(not feature))
        return self.step('uhid', {'raw_hex': event.hex()})

    def constant(self, level=16000):
        self.pid(bytes.fromhex('54010000'), feature=True)
        block = self.session.core.engine.last_block
        self.pid(bytes([0x55, block])+struct.pack('<h', level))
        return self.pid(bytes([0x5a, block, 1, 1]))

    def finish(self, complete=True):
        self.step('close', {'reason': 'test'})
        self.writer.append(dict(kind='footer', complete=complete, summary=self.session.summary()))
        self.writer.flush()

    def rewrite(self, values):
        previous = '0'*64
        lines = []
        for i, record in enumerate(values):
            record = dict(record, seq=i, previous=previous)
            record.pop('sha256', None)
            record['sha256'] = previous = digest(record)
            lines.append(canonical(record))
        self.path.write_text('\n'.join(lines)+'\n')

    def load(self):
        self.writer.flush()
        return [json.loads(s) for s in self.path.read_text().splitlines()]

    def test_synthetic_complete_race_sequence_replays_all_contracts(self):
        fixture = json.loads((ROOT / 'tests/fixtures/passive_race.json').read_text())
        self.assertIn('SYNTHETIC', fixture['provenance'])
        results = []
        for item in fixture['steps']:
            kind, data = item['action'], copy.deepcopy(item.get('data', {}))
            if kind == 'pid':
                packet = bytes.fromhex(data['packet_hex'])
                raw = output(packet, data['rtype']) if data['via'] == 'output' else set_report(
                    packet, data['request_id'], data['rtype'])
                kind, data = 'uhid', dict(raw_hex=raw.hex())
            elif kind == 'get':
                kind, data = 'uhid', dict(raw_hex=get_report(data['number'], data['request_id'], data['rtype']).hex())
            results.append(self.step(kind, data, ms=item['ms']))
        self.writer.append(dict(kind='footer', complete=True, summary=self.session.summary()))
        self.writer.flush()
        result = replay(self.path)
        self.assertTrue(result['complete'])
        self.assertGreater(result['nonzero_force_samples'], 10)
        self.assertEqual(result['summary']['counts']['created'], 3)
        self.assertEqual(result['summary']['counts']['freed'], 3)
        self.assertEqual(result['summary']['counts']['invalid'], 0)
        self.assertEqual(result['summary']['transport']['duplicates'], 1)
        self.assertEqual(result['summary']['allocated'], 0)
        self.assertFalse(result['race_physically_validated'])
        self.assertEqual(bytes.fromhex(results[2]['replies_hex'][0])[12:], bytes.fromhex('57000a2801'))
        self.assertEqual(bytes.fromhex(results[4]['replies_hex'][0])[12:], bytes.fromhex('560101c009'))
        self.assertTrue(any(r['components'].get('spring') for r in results))
        self.assertTrue(any(r['components'].get('periodic') for r in results))
        self.assertGreater(result['summary']['duration_s'], 600)

    def test_full_event_and_payload_survive_without_truncation(self):
        self.constant()
        raw = output(bytes.fromhex('5501ff7f')) + bytes([0xa5])*277
        self.assertEqual(len(raw), 4380)
        value = self.step('uhid', {'raw_hex': raw.hex()})
        self.assertEqual(value['info']['packet_hex'], '5501ff7f')
        self.finish()
        stored = self.load()[-3]
        self.assertEqual(bytes.fromhex(stored['data']['raw_hex']), raw)
        self.assertTrue(replay(self.path)['complete'])

    def test_duplicate_control_request_is_not_reapplied_but_equal_outputs_restart(self):
        self.constant()
        raw = set_report(bytes.fromhex('5a010101'), 100)
        self.step('uhid', {'raw_hex': raw.hex()})
        started = self.session.core.engine.effects[1]['started_at']
        duplicate = self.step('uhid', {'raw_hex': raw.hex()})
        self.assertEqual(self.session.core.engine.effects[1]['started_at'], started)
        self.assertEqual(duplicate['reason'], 'duplicate_request')
        self.pid(bytes.fromhex('5a010101'), via_output=True)
        self.assertGreater(self.session.core.engine.effects[1]['started_at'], started)
        self.finish()
        self.assertTrue(replay(self.path)['complete'])

    def test_repeated_force_does_not_upload_and_zero_transition_is_immediate(self):
        self.constant()
        self.step('tick', ms=100)
        uploads = self.session.core.output.writes
        for ms in (130, 160, 190, 220, 250):
            self.assertEqual(self.step('tick', ms=ms)['uploads'], 0)
        self.assertEqual(self.session.core.output.writes, uploads)
        zero = self.pid(bytes.fromhex('5003'))
        self.assertEqual(zero['final_force'], 0)
        self.assertEqual(zero['plays'], 0)
        self.finish()
        self.assertTrue(replay(self.path)['complete'])

    def test_pool_cycles_full_and_invalid_blocks_are_visible(self):
        for _ in range(40):
            self.pid(bytes.fromhex('54010000'), feature=True)
        result = self.pid(bytes.fromhex('54010000'), feature=True)
        self.assertEqual(result['transitions']['pool']['status'], 2)
        self.assertEqual(self.session.summary()['counts']['allocation_failures'], 1)
        self.assertEqual(self.pid(bytes.fromhex('5a290101'))['error'], 'pid_missing_block')
        for _ in range(10):
            self.pid(bytes.fromhex('5b01'))
            self.pid(bytes.fromhex('54010000'), feature=True)
            self.assertEqual(self.session.core.engine.last_block, 1)
        self.finish()
        self.assertTrue(replay(self.path)['complete'])

    def test_malformed_reports_and_wrong_types_keep_ids_and_errno(self):
        for raw in (set_report(b'\x55', 42), set_report(bytes.fromhex('54010000'), 43, 3)):
            result = self.step('uhid', {'raw_hex': raw.hex()})
            reply = bytes.fromhex(result['replies_hex'][0])
            self.assertEqual(reply[4:8], raw[4:8])
            self.assertNotEqual(struct.unpack_from('=H', reply, 8)[0], 0)
            self.assertIsNotNone(result['error'])
        self.finish()
        self.assertTrue(replay(self.path)['complete'])

    def test_input_get_and_pid_async_state_preserve_response_order(self):
        initial = self.step('input', dict(state=[0, 128, 30, -1, 1, [288]]))
        result = self.step('uhid', dict(raw_hex=get_report(1, 987, 2).hex()))
        reply = bytes.fromhex(result['writes_hex'][0])
        self.assertEqual(struct.unpack_from('=IIHH', reply), (10, 987, 0, 11))
        self.assertEqual(reply[12:].hex(), initial['info']['input_report_hex'])
        started = self.constant()
        kinds = [struct.unpack_from('=I', bytes.fromhex(h))[0] for h in started['writes_hex']]
        self.assertEqual(kinds, [14, 12])
        self.assertEqual(started['pid_state_hex'], '50fe01')
        self.finish()
        self.assertTrue(replay(self.path)['complete'])

    def test_clipping_cancellation_and_strength_limit(self):
        for _ in range(2):
            self.constant(32767)
        saturated = self.step('tick', ms=100)
        self.assertTrue(saturated['mix']['clipped'])
        self.assertEqual(saturated['renderer_total'], 65534)
        limited = self.step('strength', {'percent': 1}, ms=101)
        self.assertLessEqual(abs(limited['final_force']), 328)
        self.constant(-32768)
        net = self.step('tick', ms=150)
        self.assertFalse(net['mix']['clipped'])
        self.assertEqual(net['renderer_total'], 32766)  # 2*32767 - 32768, signed PID.
        self.step('strength', {'percent': 0}, ms=151)
        self.assertEqual(self.session.core.output.dev.level, 0)
        self.finish()
        self.assertTrue(replay(self.path)['complete'])

    def test_motion_samples_use_sample_clock_not_render_frequency_and_expire(self):
        self.step('input', dict(state=[8192, 255, 255, 0, 0, []], sample_ns=ORIGIN), ms=1)
        motion = self.step('input', dict(state=[12000, 255, 255, 0, 0, []],
                                       sample_ns=ORIGIN+20_000_000), ms=50)['motion']
        self.assertGreater(motion['velocity'], 0)
        for ms in (51, 52, 53):
            self.assertEqual(self.step('tick', ms=ms)['motion'], motion)
        self.assertEqual(self.step('tick', ms=300)['motion']['velocity'], 0)
        self.finish()
        self.assertTrue(replay(self.path)['complete'])

    def test_pause_restart_and_close_open_probe_reset(self):
        self.constant()
        self.pid(bytes.fromhex('5005'))
        self.assertEqual(self.session.summary()['paused'], 1)
        self.pid(bytes.fromhex('5a010101'))
        started = self.session.core.engine.effects[1]['started_at']
        self.step('tick', ms=10000)
        self.pid(bytes.fromhex('5006'))
        self.assertEqual(self.session.core.engine.effects[1]['started_at'], started)
        self.assertNotEqual(self.step('tick', ms=10040)['final_force'], 0)
        self.step('uhid', dict(raw_hex=struct.pack('=I', 5).hex()))
        self.assertFalse(self.session.core.closed)
        self.assertEqual(self.session.core.output.dev.level, 0)
        self.step('uhid', dict(raw_hex=struct.pack('=I', 4).hex()))
        self.constant()
        self.finish()
        self.assertTrue(replay(self.path)['complete'])

    def test_loss_duplicate_reorder_and_truncation_are_detected(self):
        self.constant()
        self.finish()
        values = self.load()
        originals = self.path.read_text()
        lines = originals.splitlines(keepends=True)
        for corrupted in (lines[:2]+lines[3:], lines[:2]+[lines[1]]+lines[2:],
                          lines[:1]+[lines[2], lines[1]]+lines[3:], [originals[:-4]]):
            self.path.write_text(''.join(corrupted))
            with self.assertRaises(ValueError):
                replay(self.path)
        self.assertEqual(values[0]['metadata']['backend'], 'mock')

    def test_semantic_tampering_detected_even_after_recomputing_chain(self):
        self.constant()
        self.finish()
        values = self.load()
        for key, value in [('final_force', 123), ('state_sha256', 'fake'), ('replies_hex', []),
                           ('pid_state_hex', '500201')]:
            changed = copy.deepcopy(values)
            changed[-3]['result'][key] = value
            self.rewrite(changed)
            with self.assertRaisesRegex(ValueError, 'Replay divergente'):
                replay(self.path)

    def test_missing_command_with_valid_hash_chain_is_detected_by_state(self):
        self.constant()
        self.finish()
        values = self.load()
        self.rewrite(values[:2]+values[3:])  # Remove magnitude, preserva Start/expected output.
        with self.assertRaisesRegex(ValueError, 'Replay divergente'):
            replay(self.path)

    def test_unexpected_shutdown_only_verifies_prefix_and_mock_expires(self):
        self.constant()
        self.writer.flush()
        prefix = replay(self.path)
        self.assertFalse(prefix['complete'])
        self.assertTrue(prefix['verified'])
        self.session.timestamp_ns += 200_000_000  # Simula desaparecimento, sem close/tick.
        self.assertEqual(self.session.core.output.dev.level, 0)

    def test_ack_delivery_precedes_capture_and_failures_are_recorded(self):
        order = []
        def send(data):
            order.append('send')
            if len(order) > 1:
                raise OSError('synthetic I/O failure')
        recorder = Recorder(self.session, self.writer, send)
        with self.assertRaises(OSError):
            recorder.dispatch('uhid', dict(raw_hex=get_report(0x57, 1).hex()), ORIGIN+1)
        stored = self.load()[-1]
        self.assertEqual(stored['delivery']['sent'], 1)
        self.assertIn('synthetic', stored['delivery']['error'])
        self.assertFalse(replay(self.path)['complete'])

    def test_capture_never_overwrites_and_version_or_descriptor_mismatch_rejected(self):
        with self.assertRaises(FileExistsError):
            CaptureWriter(self.path, {})
        self.finish()
        values = self.load()
        for key in ('descriptor_hex', 'implementation_sha256'):
            changed = copy.deepcopy(values)
            changed[0]['metadata'][key] = 'wrong'
            self.rewrite(changed)
            with self.assertRaisesRegex(ValueError, 'incompatível'):
                replay(self.path)

    def test_gzip_capture_is_streamed_and_replay_checks_simulated_actions(self):
        path = Path(self.directory) / 'compressed.jsonl.gz'
        writer = CaptureWriter(path, self.session.metadata())
        self.addCleanup(writer.close)
        recorder = Recorder(self.session, writer)
        recorder.dispatch('uhid', dict(raw_hex=set_report(bytes.fromhex('54010000'), 1, 0).hex()), ORIGIN+1)
        recorder.dispatch('uhid', dict(raw_hex=output(bytes.fromhex('5501803e')).hex()), ORIGIN+2)
        result = recorder.dispatch('uhid', dict(raw_hex=output(bytes.fromhex('5a010101')).hex()), ORIGIN+3)
        self.assertEqual([v[0] for v in result['mock_actions']], ['upload', 'write', 'syn'])
        self.assertTrue(result['mock']['playing'])
        recorder.dispatch('close', {}, ORIGIN+4)
        writer.append(dict(kind='footer', complete=True, summary=self.session.summary()))
        writer.close()
        self.assertTrue(replay(path)['complete'])
        with gzip.open(path, 'rt') as stream:
            self.assertIn('54010000', stream.read())

    def test_ignored_nonsteering_condition_is_explicit_in_capture(self):
        self.pid(bytes.fromhex('54090000'), feature=True)
        result = self.pid(bytes([0x53, 1, 1])+bytes(12))
        self.assertEqual(result['renderer_ignored'], {'condition_nonsteering_axis': 1})
        self.assertIn('ignored:', result['reason'])
        self.finish()
        self.assertTrue(replay(self.path)['complete'])

    def test_raw_metadata_recovers_request_report_types_and_ids(self):
        raw = set_report(bytes.fromhex('54010000'), 0x12345678, 0)
        info = describe_uhid(raw)
        self.assertEqual((info['event_type'], info['request_id'], info['report_type'], info['report_id']),
                         (13, 0x12345678, 0, 0x54))
        self.assertEqual(describe_uhid(b'bad')['event_type'], None)

    def test_clock_rollback_and_invalid_samples_are_rejected(self):
        self.step('tick', ms=1)
        with self.assertRaises(ValueError):
            self.session.apply('tick', {}, ORIGIN)
        with self.assertRaises(ValueError):
            self.step('input', dict(state=[16384, 255, 255, 0, 0, []]))

    def test_phase_is_annotation_not_false_producer_attribution(self):
        self.assertEqual(self.constant()['producer'], 'unattributed')
        marker = self.step('mark', {'phase': 'game_operator_marked'})
        self.assertEqual(marker['producer'], 'unattributed')
        self.assertEqual(marker['phase'], 'game_operator_marked')
        self.finish()
        self.assertTrue(replay(self.path)['complete'])
