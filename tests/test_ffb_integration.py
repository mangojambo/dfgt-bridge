"""Sequências completas em memória. OfflineSession não abre UHID/EVDEV/USB."""
import csv
import errno
import itertools
import struct
import unittest

from test_ffb_audit import FIXTURES
from test_ffb_transport import get_report, output, set_report
from dfgt_bridge.backends.mock import MockBackend
from dfgt_bridge.ffb.session import OfflineSession


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.now = 0.0
        self.session = OfflineSession(clock=lambda: self.now)
        self.requests = itertools.count(1)
        self.addCleanup(self.session.close)

    def send(self, packet, *, feature=False, via_output=False, observation_id=None):
        event = (output(packet, int(not feature)) if via_output else
                 set_report(packet, next(self.requests), int(not feature)))
        return self.session.handle(event, observation_id=observation_id)

    def constant(self, level=16000, duration=0xffff, delay=0, loops=1):
        self.send(bytes.fromhex('54010000'), feature=True)
        block = self.session.engine.last_block
        self.send(bytes((0x51, block, 1)) + struct.pack(
            '<HHHHBBBHH', duration, 0, 0, delay, 100, 255, 4, 9000, 0))
        self.send(bytes((0x55, block)) + struct.pack('<h', level))
        self.send(bytes((0x5a, block, 1, loops)))
        return block

    def test_complete_create_configure_play_stop_free_sequence(self):
        block = self.constant()
        self.assertEqual(self.session.output.dev.level, 3200)
        reply = self.session.handle(get_report(0x50, next(self.requests), 2))[0]
        self.assertEqual(reply[12:], bytes((0x50, 6, block)))
        self.send(bytes((0x5a, block, 3, 0)))
        self.assertEqual(self.session.output.dev.level, 0)
        self.send(bytes((0x5b, block)))
        self.assertEqual(self.session.engine.effects, {})
        self.assertEqual(self.session.engine.pool.available, 2560)

    def test_multiple_effects_solo_and_zero_transition_preserve_composition(self):
        first = self.constant(10000)
        second = self.constant(8000)
        self.now = .03
        self.session.tick()
        self.assertEqual(self.session.output.dev.level, 3600)
        self.send(bytes((0x5a, second, 2, 1)))
        self.assertFalse(self.session.engine.effects[first]['running'])
        self.now = .06
        self.session.tick()
        self.assertEqual(self.session.output.dev.level, 1600)
        self.send(bytes((0x50, 3)))
        self.assertEqual(self.session.output.dev.level, 0)

    def test_pause_resume_and_restart_keep_separate_timelines(self):
        block = self.constant(duration=100, delay=50, loops=3)
        self.now = .075
        self.session.tick()
        self.assertNotEqual(self.session.output.dev.level, 0)
        self.send(bytes((0x50, 5)))
        self.assertEqual(self.session.output.dev.level, 0)
        self.now = 10
        self.send(bytes((0x50, 6)))
        self.assertNotEqual(self.session.output.dev.level, 0)
        self.now = 10.1
        self.send(bytes((0x5a, block, 1, 1)))
        self.assertEqual(self.session.output.dev.level, 0)  # Novo delay.
        self.now = 10.16
        self.session.tick()
        self.assertNotEqual(self.session.output.dev.level, 0)
        self.now = 10.25
        self.session.tick()
        self.assertEqual(self.session.output.dev.level, 0)

    def test_stop_then_start_inside_same_tick_never_loses_stop(self):
        block = self.constant()
        self.send(bytes((0x5a, block, 3, 0)), via_output=True)
        self.assertEqual(self.session.output.dev.level, 0)
        self.send(bytes((0x5a, block, 1, 1)), via_output=True)
        self.now = .03
        self.session.tick()
        self.assertEqual(self.session.output.dev.level, 3200)
        self.assertIn(('write', (0x15, 7, 0)), self.session.output.dev.actions)

    def test_disable_enable_and_reset_act_on_output_immediately(self):
        self.constant()
        self.send(bytes((0x50, 2)))
        self.assertEqual(self.session.output.dev.level, 0)
        self.now = .03
        self.send(bytes((0x50, 1)))
        self.assertEqual(self.session.output.dev.level, 3200)
        self.send(bytes((0x50, 4)))
        self.assertEqual(self.session.output.dev.level, 0)
        self.assertEqual(self.session.engine.effects, {})

    def test_pool_full_and_many_releases_remain_bounded(self):
        for _ in range(40):
            self.send(bytes.fromhex('54010000'), feature=True)
        self.send(bytes.fromhex('54010000'), feature=True)
        self.assertEqual(self.session.engine.pool.last.status, 2)
        for block in range(1, 41):
            self.send(bytes((0x5b, block)))
        for _ in range(500):
            self.send(bytes.fromhex('54010000'), feature=True)
            self.assertEqual(self.session.engine.last_block, 1)
            self.send(bytes((0x5b, 1)))
        self.assertEqual(len(self.session.engine.effects), 0)
        self.assertLessEqual(len(self.session.transport.requests), 128)

    def test_correlated_cross_path_start_is_not_applied_twice(self):
        block = self.constant()
        self.now = .01
        packet = bytes((0x5a, block, 1, 1))
        self.send(packet, via_output=True, observation_id=123)
        self.now = .02
        self.send(packet, observation_id=123)
        self.assertEqual(self.session.engine.effects[block]['started_at'], .01)

    def test_many_same_force_reports_do_not_reupload_or_accumulate_commands(self):
        block = self.constant()
        self.now = .03
        self.session.tick()
        before = self.session.output.writes
        for index in range(10000):
            self.now = .03+index/1_000_000
            self.send(bytes((0x55, block)) + struct.pack('<h', 16000), via_output=True)
        self.assertEqual(self.session.output.writes, before)
        self.assertLess(len(self.session.output.dev.actions), 10)

    def test_malformed_pid_is_nacked_without_silent_state_changes(self):
        block = self.constant()
        result = self.send(bytes((0x55, block, 1)))[0]
        self.assertEqual(struct.unpack_from('=H', result, 8)[0], errno.EINVAL)
        self.assertEqual(self.session.engine.effects[block]['level'], 16000)

    def test_close_event_stops_erases_and_prevents_old_state_reactivation(self):
        self.constant()
        self.session.handle(struct.pack('=I', 5))
        self.assertTrue(self.session.closed)
        self.assertEqual(self.session.output.dev.level, 0)
        self.assertIsNone(self.session.output.dev.effect)
        self.session.handle(struct.pack('=I', 4))
        self.session.tick()
        self.assertFalse(self.session.engine.armed)

    def test_exception_exit_cleans_up_without_suppressing_the_exception(self):
        with self.assertRaisesRegex(RuntimeError, 'falha simulada'):
            with self.session:
                self.constant()
                raise RuntimeError('falha simulada')
        self.assertTrue(self.session.closed)
        self.assertEqual(self.session.output.dev.level, 0)
        self.assertIsNone(self.session.output.dev.effect)

    def test_rendering_failure_stops_active_output(self):
        self.constant()
        self.now = float('nan')
        with self.assertRaises(ValueError):
            self.session.tick()
        self.assertTrue(self.session.closed)
        self.assertEqual(self.session.output.dev.level, 0)

    def test_abrupt_process_disappearance_is_modeled_by_independent_mock_expiry(self):
        self.constant()
        backend = self.session.output.dev
        self.now = 1  # Nenhum tick nem close foi executado nesse intervalo.
        self.assertEqual(backend.level, 0)
        self.assertFalse(self.session.closed)

    def test_offline_session_refuses_non_mock_backends(self):
        with self.assertRaises(ValueError):
            OfflineSession(backend=object())

    def test_real_corpus_runs_through_the_full_pipeline_without_any_force_upload(self):
        total = 0
        for path in sorted(FIXTURES.glob('*.csv')):
            self.session.close()
            self.session = OfflineSession(clock=lambda: self.now)
            self.addCleanup(self.session.close)
            with path.open() as handle:
                for row in csv.DictReader(handle):
                    total += 1
                    self.now = int(row['timestamp_ns'])/1e9
                    packet = bytes.fromhex(row['hex_bytes'])
                    rtype = int(row['report_type'])
                    if row['kind'] == 'OUTPUT':
                        event = output(packet, rtype)
                    elif row['kind'] == 'SET_REPORT':
                        event = set_report(packet, total, rtype, int(row['report_num']))
                    else:
                        event = get_report(int(row['report_num']), total, rtype)
                    self.session.handle(event)
                    self.assertEqual(self.session.output.dev.level, 0)
            self.assertEqual(self.session.output.writes, 0)
        self.assertEqual(total, 132)
