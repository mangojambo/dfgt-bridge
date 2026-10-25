"""Runner opt-in com supervisor falso; modo passivo nunca instancia escritor."""
import json
import unittest
from unittest.mock import Mock

import test_passive_runner as passive_tests
from dfgt_bridge import runner


class FakePhysical(passive_tests.FakeResource):
    def __init__(self):
        self.frames = []
        self.commands = []
    def publish(self, result, session):
        self.frames.append((session.timestamp_ns, dict(result['components'])))
    def service(self):
        pass
    def command(self, action, data):
        self.commands.append((action, data))
    def diagnostic(self):
        return dict(closed=self.closed, evdev_level=0)
    def close(self):
        self.closed = True


class PhysicalIntegrationTests(unittest.TestCase):
    setUp = passive_tests.RunnerTests.setUp
    clock = passive_tests.RunnerTests.clock
    execute = passive_tests.RunnerTests.execute
    assert_closed = passive_tests.RunnerTests.assert_closed
    def test_passive_never_constructs_physical_backend_even_with_factory_available(self):
        factory = Mock(side_effect=AssertionError('physical backend in passive mode'))
        self.execute(physical_factory=factory)
        factory.assert_not_called()
        self.assertTrue(self.path.exists())

    def test_opt_in_uses_ten_percent_without_changing_core_or_inputs(self):
        self.args.enable_ffb = True
        physical = FakePhysical()
        factory = Mock(return_value=physical)
        self.execute(physical_factory=factory)
        self.assertTrue(factory.call_args.kwargs['enable_ffb'])
        self.assertEqual(factory.call_args.kwargs['config'], dict(strength=10, maximum=20, polarity=1, session_s=120, require_game=True, slew_per_s=16383.5))
        self.assertTrue(physical.frames)
        self.assertTrue(physical.closed)
        footer = json.loads(self.path.read_text().splitlines()[-1])
        self.assertTrue(footer['runtime']['physical']['closed'])
        header = json.loads(self.path.read_text().splitlines()[0])
        self.assertEqual(header['metadata']['physical_output'], 'opt_in_evdev')

    def test_supervisor_failure_closes_virtual_and_input_resources(self):
        self.args.enable_ffb = True
        physical = FakePhysical()
        physical.publish = Mock(side_effect=OSError('worker disconnected'))
        with self.assertRaises(OSError):
            self.execute(physical_factory=Mock(return_value=physical))
        self.assertTrue(physical.closed)
        self.assert_closed()

    def test_physical_flags_are_not_enablement(self):
        from unittest.mock import patch
        with patch.object(runner, 'run') as run, patch.object(runner.signal, 'signal'):
            self.assertEqual(runner.main(['--ffb-polarity', '-1']), 0)
        self.assertFalse(run.call_args.args[0].enable_ffb)

    def test_explicit_full_scale_and_slew_reach_physical_process_configuration(self):
        self.args.enable_ffb = True
        self.args.ffb_strength = self.args.ffb_max_strength = 100
        self.args.ffb_slew_rate = 327670
        factory = Mock(return_value=FakePhysical())
        self.execute(physical_factory=factory)
        self.assertEqual(factory.call_args.kwargs['config']['strength'], 100)
        self.assertEqual(factory.call_args.kwargs['config']['maximum'], 100)
        self.assertEqual(factory.call_args.kwargs['config']['slew_per_s'], 327670)

    def test_gui_control_eof_closes_runner_and_physical_client(self):
        from unittest.mock import patch
        self.args.enable_ffb = self.args.control_stdin = True
        physical = FakePhysical()
        with patch('sys.stdin.fileno', return_value=999), patch('os.read', return_value=b''):
            self.execute(physical_factory=Mock(return_value=physical))
        self.assertTrue(physical.closed)
        self.assert_closed()
        footer = json.loads(self.path.read_text().splitlines()[-1])
        self.assertEqual(footer['reason'], 'GUI_control_EOF')

    def test_service_status_is_not_ready_until_physical_backend_acknowledges(self):
        self.args.enable_ffb=True
        physical=FakePhysical();counter={'n':0};events=[]
        def diagnostic():
            counter['n']+=1
            return dict(ready=counter['n']>=3,closed=physical.closed,evdev_level=0)
        physical.diagnostic=diagnostic
        self.binder.last=dict(self.binder.last,ff_input=['fake-event'])
        self.execute(physical_factory=Mock(return_value=physical),status_callback=lambda v:events.append((counter['n'],v)))
        self.assertEqual(len(events),1)
        self.assertGreaterEqual(events[0][0],3)
        self.assertTrue(events[0][1]['physical_ready'])
        self.assertEqual(events[0][1]['binding']['driver'],'hid-universal-pidff')

    def test_service_scope_is_forwarded_without_affecting_default_physical_profile(self):
        self.args.enable_ffb=True;self.args.game_uid=1000;self.args.game_after_ticks=123
        factory=Mock(return_value=FakePhysical())
        self.execute(physical_factory=factory)
        self.assertEqual(factory.call_args.kwargs['game_scope'],dict(uid=1000,after_ticks=123))
