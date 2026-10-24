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
        self.assertEqual(factory.call_args.kwargs['config'], dict(strength=10, maximum=20, polarity=1, session_s=120, require_game=True))
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
