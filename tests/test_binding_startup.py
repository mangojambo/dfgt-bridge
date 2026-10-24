"""Enumeração simulada atende GET/SET sem deadlock e bloqueia saída física."""
import json
import struct
import unittest
from unittest.mock import Mock, patch

from test_ffb_audit import ROOT
from test_ffb_transport import get_report, set_report
import test_passive_runner as passive_tests
from test_physical_integration import FakePhysical
from dfgt_bridge import runner
from dfgt_bridge.pid_binding import BindingError
from dfgt_bridge.replay import replay


class StartupRunnerTests(unittest.TestCase):
    setUp = passive_tests.RunnerTests.setUp
    clock = passive_tests.RunnerTests.clock
    execute = passive_tests.RunnerTests.execute
    assert_closed = passive_tests.RunnerTests.assert_closed

    def test_failed_logitech_stop_does_not_kill_pidff_get_set_handshake(self):
        self.args.enable_ffb = True
        self.virtual.events = [struct.pack('=I', 3), struct.pack('=I', 2),
                               get_report(0x57, 50), set_report(bytes.fromhex('54010000'), 51, 0),
                               get_report(0x56, 52), set_report(bytes.fromhex('5b01'), 53)]
        def poll():
            replies = [struct.unpack_from('=II', w) for w in self.virtual.writes
                       if struct.unpack_from('=I', w)[0] in (10, 14)]
            return all(value in replies for value in ((10, 50), (14, 51), (10, 52), (14, 53)))
        self.binder.poll = poll
        physical = FakePhysical()
        factory = Mock(side_effect=lambda *args, **kwargs: physical if poll() else self.fail('physical before PIDFF ACK'))
        summary = self.execute(physical_factory=factory)
        self.assertEqual(summary['transport']['reads'], 2)
        self.assertEqual(summary['counts']['created'], 1)
        self.assertEqual(summary['counts']['freed'], 1)
        factory.assert_called_once()
        self.assertIn('[PRONTO]', self.console.getvalue())
        self.assertTrue(replay(self.path)['complete'])
        self.assert_closed()

    def test_binding_failure_does_not_start_physical_process_or_claim_ready(self):
        self.args.enable_ffb = True
        self.binder.poll = Mock(side_effect=BindingError('logitech -22; driver unbound; sem EVDEV/FF'))
        factory = Mock(side_effect=AssertionError('must not open physical backend'))
        with self.assertRaisesRegex(BindingError, 'logitech -22'):
            self.execute(physical_factory=factory)
        factory.assert_not_called()
        self.assertNotIn('[PRONTO]', self.console.getvalue())
        self.assert_closed()

    def test_real_binding_job_is_unblocked_by_main_loop_get_reply(self):
        import threading
        import test_pid_binding as fixtures
        env = fixtures.BindingTests()
        env.setUp()
        self.addCleanup(env.doCleanups)
        env.device()
        self.args.duration = 1.0
        self.virtual.events = [struct.pack('=I', 3)]
        acknowledged = threading.Event()
        original_write = self.virtual.write
        def reply(data):
            original_write(data)
            if len(data) >= 8 and struct.unpack_from('=II', data) == (10, 99):
                acknowledged.set()
        self.virtual.write = reply
        original_sysfs_write = env.write
        def sysfs_write(path, data):
            if path.name != 'new_id':
                return original_sysfs_write(path, data)
            self.virtual.events.append(get_report(0x57, 99))
            if not acknowledged.wait(1):
                raise TimeoutError('GET_REPORT aguardando main loop')
            env.writes.append((path, data))
            env.bound()
        env.binder.writer = sysfs_write
        self.execute(binding_factory=lambda **kwargs: env.binder)
        self.assertTrue(acknowledged.is_set())
        self.assertTrue(env.binder.ready)
        self.assertIn('[PRONTO]', self.console.getvalue())
        self.assertTrue(replay(self.path)['complete'])

    def test_signal_during_final_ready_check_cannot_start_physical_process(self):
        self.args.enable_ffb = True
        stop = {'reason': None}
        def ready():
            stop['reason'] = 'SIGTERM'
            return True
        self.binder.poll = ready
        factory = Mock(side_effect=AssertionError('physical start during shutdown'))
        self.execute(stop=stop, physical_factory=factory)
        factory.assert_not_called()
        self.assertNotIn('[PRONTO]', self.console.getvalue())

    def test_driver_absent_fails_before_virtual_creation(self):
        self.binder.prepare = Mock(side_effect=BindingError('hid-universal-pidff ausente'))
        with self.assertRaisesRegex(BindingError, 'ausente'):
            self.execute()
        self.assertTrue(self.wheel.closed)
        self.assertFalse(self.virtual.closed)
        self.assertNotIn('[PRONTO]', self.console.getvalue())

    def test_timeout_is_clear_and_cleans_up_without_retrying_forever(self):
        self.binder.poll = Mock(side_effect=[False, BindingError('Timeout de associação PIDFF')])
        with self.assertRaisesRegex(BindingError, 'Timeout'):
            self.execute()
        self.assert_closed()
        self.assertNotIn('[PRONTO]', self.console.getvalue())

    def test_binding_loss_after_ready_closes_supervisor_and_virtual_device(self):
        self.args.enable_ffb = True
        self.binder.poll = Mock(side_effect=[True, BindingError('G29 desconectado')])
        physical = FakePhysical()
        with self.assertRaisesRegex(BindingError, 'desconectado'):
            self.execute(physical_factory=Mock(return_value=physical))
        self.assertTrue(physical.closed)
        self.assert_closed()
        footer = json.loads(self.console.getvalue().splitlines()[-1])
        self.assertFalse(footer['runtime']['ready'])

    def test_shutdown_cancels_before_destroy_and_joins_after_destroy(self):
        order = []
        self.binder.cancel = lambda: order.append('cancel')
        self.binder.finish = lambda: order.append('finish')
        original_exit = type(self.virtual).__exit__
        def closed(device, *args):
            order.append('destroy')
            original_exit(device, *args)
        with patch.object(type(self.virtual), '__exit__', closed):
            self.execute(stop={'reason': 'SIGTERM'})
        self.assertEqual(order, ['cancel', 'destroy', 'finish'])
        self.assertNotIn('[PRONTO]', self.console.getvalue())

    def test_legacy_command_flags_are_accepted_and_binding_is_default(self):
        with patch.object(runner, 'run') as run, patch.object(runner.signal, 'signal'):
            self.assertEqual(runner.main(['--identity', 'g29', '--auto-pidff', '--gas-axis', 'y']), 0)
        self.assertTrue(run.call_args.args[0].auto_pidff)
        self.assertFalse(run.call_args.args[0].enable_ffb)

    def test_binding_control_is_explicitly_captured_for_deterministic_replay(self):
        self.execute()
        records = [json.loads(line) for line in self.path.read_text().splitlines()]
        transitions = [r['data'] for r in records if r.get('action') == 'binding']
        self.assertEqual(transitions, [dict(ready=False), dict(ready=True)])
        self.assertTrue(records[-1]['runtime']['ready'])
        self.assertTrue(replay(self.path)['complete'])

