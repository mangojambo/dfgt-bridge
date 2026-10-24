"""Processo supervisor testado com relógio, soquetes, backend e teclas falsos."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from test_ffb_audit import ROOT
from dfgt_bridge.backends.mock import MockBackend
from dfgt_bridge.ffb.effects import OUTPUT_COMPONENTS
from dfgt_bridge import physical_worker as worker
from dfgt_bridge.physical_replay import replay_physical


class Channel:
    def __init__(self, messages=()):
        self.messages, self.sent, self.closed = list(messages), [], False
    def recv(self, size):
        if not self.messages:
            raise BlockingIOError()
        return self.messages.pop(0)
    def send(self, value):
        self.sent.append(value)
        return len(value)
    def setblocking(self, value):
        pass
    def close(self):
        self.closed = True


class LocalMailbox:
    value = None
    def read(self):
        return self.value
    def write(self, value):
        self.value = value


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.directory = self.enterContext(tempfile.TemporaryDirectory())
        self.path = Path(self.directory) / 'physical.jsonl.gz'
        self.channel = Channel()
        self.status, self.commands = LocalMailbox(), LocalMailbox()
        self.epoch, self.emergency, self.quit = SimpleNamespace(value=0), SimpleNamespace(value=0), SimpleNamespace(value=0)
        self.now = 0
        self.backend = MockBackend(clock=lambda: self.now/1e9)
        self.backend.close = Mock()
        self.keys = SimpleNamespace(devices={81: 'fake'}, read=Mock(return_value=[]), close=Mock())
        self.enterContext(patch('dfgt_bridge.backends.evdev.EvdevBackend', return_value=self.backend))
        self.enterContext(patch('dfgt_bridge.input.global_keys.GlobalKeys', return_value=self.keys))
        self.enterContext(patch('signal.signal'))
        self.enterContext(patch('time.sleep'))
        self.enterContext(patch('time.monotonic_ns', side_effect=self.clock))
        self.stdout = self.enterContext(contextlib.redirect_stdout(io.StringIO()))

    def clock(self):
        self.now += 1_000_000
        if self.now >= 200_000_000:
            self.quit.value = 1
        return self.now

    def run_worker(self, require_game=False):
        worker.worker(self.channel, self.status, self.commands, self.epoch, self.emergency,
                      self.quit, 'fake', dict(strength=10, maximum=20, polarity=1, session_s=120,
                      require_game=require_game), None, str(self.path))

    def frame(self, generation=1, epoch=0):
        return json.dumps(dict(generation=generation, calculated_ns=1_000_000, stop_epoch=epoch,
                          components={k: 32767 if k == 'constant' else 0 for k in OUTPUT_COMPONENTS})).encode()

    def test_worker_latches_if_parent_stops_and_trace_replays_offline(self):
        self.channel.messages = [self.frame()]
        self.run_worker()
        self.assertTrue(self.channel.closed)
        self.backend.close.assert_called_once()
        result = replay_physical(self.path)
        self.assertTrue(result['complete'])
        self.assertEqual(result['frames'], 1)
        self.assertGreaterEqual(result['summary']['safety_events'], 1)
        self.assertEqual(self.backend.level, 0)

    def test_keys_and_terminal_without_keyboard_both_stop_safely(self):
        self.keys.devices = {}
        self.channel.messages = [self.frame()]
        self.run_worker()
        self.assertIn('IN DISPONÍVEIS'.replace(' ', ''), self.stdout.getvalue())
        self.assertIn('kill -USR1', self.stdout.getvalue())
        self.assertTrue(replay_physical(self.path)['complete'])

    def test_f9_never_rearms_even_when_f8_follows(self):
        self.channel.messages = [self.frame()]
        self.keys.read.side_effect = [[67, 66, 67, 68]] + [[]]*500
        self.run_worker()
        result = replay_physical(self.path)
        self.assertTrue(result['summary']['latched'])
        self.assertEqual(result['summary']['uploads'], 0)
        self.assertEqual(result['summary']['strength'], 15)

    def test_stop_epoch_rejects_inflight_frame_from_before_reset(self):
        self.epoch.value = 2
        self.channel.messages = [self.frame(epoch=1)]
        self.run_worker()
        result = replay_physical(self.path)
        self.assertEqual(result['summary']['uploads'], 0)
        self.assertGreater(result['summary']['rejected'], 0)

    def test_eof_closes_backend_without_pending_renewal(self):
        self.channel.messages = [b'']
        self.run_worker()
        self.backend.close.assert_called_once()
        self.assertEqual(self.backend.level, 0)
        self.assertTrue(replay_physical(self.path)['complete'])

    def test_game_exit_stops_even_if_uhid_remains_open(self):
        self.channel.messages = [self.frame()]
        game = SimpleNamespace(identity=(42, 123), identity_for=lambda pid: (42, 123),
                               poll=Mock(side_effect=[(42, 123)]+[None]*500))
        with patch('dfgt_bridge.game_monitor.GameMonitor', return_value=game):
            self.run_worker(require_game=True)
        result = replay_physical(self.path)
        self.assertTrue(result['complete'])
        self.assertTrue(result['summary']['latched'])
        self.assertEqual(result['summary']['zero_reason'], 'game_exit')
        self.assertEqual(self.backend.level, 0)

    def test_backend_failure_is_reported_and_resources_closed(self):
        self.channel.messages = [self.frame()]
        self.backend.upload_effect = Mock(side_effect=OSError('unplugged'))
        self.run_worker()
        self.assertIn('unplugged', self.status.value['fatal'])
        self.backend.close.assert_called_once()
        self.keys.close.assert_called_once()
        self.assertTrue(self.channel.closed)

    def test_terminal_stop_is_independent_of_overwritten_command_mailbox(self):
        self.emergency.value = 1
        self.channel.messages = [self.frame()]
        self.commands.value = dict(nonce=2, action='arm', data={'requested_ns': 0})
        self.run_worker()
        result = replay_physical(self.path)
        self.assertTrue(result['summary']['latched'])
        self.assertEqual(result['summary']['uploads'], 0)


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.client = worker.PhysicalClient.__new__(worker.PhysicalClient)
        self.client.channel = Channel()
        self.client.status, self.client.commands = LocalMailbox(), LocalMailbox()
        self.client.status.value = dict(at_ns=1_000_000_000, ready=True)
        self.client.process = Mock()
        self.client.process.is_alive.return_value = True
        self.client.stop_epoch = SimpleNamespace(value=0)
        self.client.emergency_epoch = SimpleNamespace(value=0)
        self.client.quit_flag = SimpleNamespace(value=0)
        self.client.ready = True
        self.client.latest = None
        self.client.generation = self.client.coalesced = self.client.nonce = 0
        self.client.cached_status = {}
        self.client.started_ns = 1_000_000_000
        self.client.closed = False
        self.client.last_zero = True
        self.enterContext(patch('time.monotonic_ns', return_value=1_000_000_001))
        self.result = dict(info={}, error=None, components=dict.fromkeys(OUTPUT_COMPONENTS, 0))
        self.session = SimpleNamespace(timestamp_ns=1_000_000_000,
                       core=SimpleNamespace(closed=False, engine=SimpleNamespace(gain=1, effects={})))

    def test_no_opt_in_cannot_spawn_or_open_writer(self):
        with patch('multiprocessing.get_context') as ctx, self.assertRaises(PermissionError):
            worker.PhysicalClient('fake')
        ctx.assert_not_called()

    def test_high_rate_producer_keeps_one_inflight_and_one_latest_without_queue(self):
        for _ in range(10000):
            self.client.publish(self.result, self.session)
        self.assertEqual(len(self.client.channel.sent), 1)
        self.assertEqual(self.client.coalesced, 9998)
        latest = json.loads(self.client.latest)
        self.assertEqual(latest['generation'], 10000)
        self.client.channel.messages = [b'ack']
        self.client.service()
        self.assertEqual(len(self.client.channel.sent), 2)
        self.assertIsNone(self.client.latest)

    def test_emergency_barrier_survives_command_overwrite(self):
        self.client.command('stop', dict(reason='terminal', latch=True))
        self.client.command('strength', dict(percent=5))
        self.assertEqual(self.client.emergency_epoch.value, 1)
        self.assertEqual(self.client.stop_epoch.value, 1)

    def test_zero_calculation_barrier_is_not_lost_when_next_force_overwrites_latest(self):
        self.result['components']['constant'] = 1000
        self.client.publish(self.result, self.session)
        self.result['components']['constant'] = 0
        self.client.publish(self.result, self.session)
        self.result['components']['constant'] = -1000
        self.client.publish(self.result, self.session)
        self.assertEqual(self.client.stop_epoch.value, 1)
        self.assertEqual(json.loads(self.client.latest)['stop_epoch'], 1)

    def test_unresponsive_child_is_detected_and_close_kills_it(self):
        with patch('time.monotonic_ns', return_value=2_000_000_000), self.assertRaises(TimeoutError):
            self.client.service()
        self.client.close()
        self.client.process.kill.assert_called_once()
        self.assertTrue(self.client.channel.closed)
        self.assertEqual(self.client.quit_flag.value, 1)

    def test_worker_death_and_channel_eof_are_not_silent(self):
        self.client.process.is_alive.return_value = False
        with self.assertRaises(OSError):
            self.client.service()

    def test_shared_mailbox_never_waits_for_a_writer_that_died_mid_update(self):
        ctx = SimpleNamespace(RawValue=lambda kind, value: SimpleNamespace(value=value),
                              RawArray=lambda kind, count: bytearray(count))
        mailbox = worker.Mailbox(ctx)
        self.assertIsNone(mailbox.read())
        mailbox.write({'safe': True})
        self.assertEqual(mailbox.read(), {'safe': True})
        mailbox.sequence.value += 1
        self.assertIsNone(mailbox.read())
        self.client.process.is_alive.return_value = True
        self.client.channel.messages = [b'']
        with self.assertRaises(OSError):
            self.client.service()

    def test_stop_reset_disable_free_close_publish_barriers(self):
        for packet in ('5002', '5003', '5004', '5005', '5a010300', '5b01'):
            self.result['info'] = dict(event_type=6, report_type=1, packet_hex=packet)
            self.client.publish(self.result, self.session)
        self.assertEqual(self.client.stop_epoch.value, 6)
        self.session.core.closed = True
        self.client.publish(self.result, self.session)
        self.assertEqual(self.client.stop_epoch.value, 7)
