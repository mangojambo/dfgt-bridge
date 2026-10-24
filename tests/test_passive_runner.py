"""Loop live com fds falsos; falhas de todas as fronteiras limpam recursos."""
import contextlib
import io
from pathlib import Path
import signal
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from test_ffb_audit import ROOT
from test_ffb_transport import get_report, output, set_report
from dfgt_bridge import runner
from dfgt_bridge.replay import replay


class FakeResource:
    closed = False
    def __enter__(self):
        return self
    def __exit__(self, *args):
        self.closed = True


class FakeWheel(FakeResource):
    fd, resyncs = 11, 0
    frame_times_ns = []
    def state(self):
        return [8192, 255, 255, 0, 0, []]
    def read_frames(self):
        return []


class FakeVirtual(FakeResource):
    fd = 12
    owner_token = 'dfgt-bridge-test-owned'
    def __init__(self, events):
        self.events, self.writes = list(events), []
    def write(self, data):
        self.writes.append(data)
    def read(self):
        return self.events.pop(0) if self.events else None


class FakeSelector(FakeResource):
    def __init__(self):
        self.keys = []
    def register(self, fd, mask, data):
        self.keys.append(SimpleNamespace(fd=fd, data=data))
    def select(self, timeout):
        return [(key, 1) for key in self.keys]


class FakeBinding:
    last = dict(node='0003:046D:C24F.FAKE', driver='hid-universal-pidff',
                evdev=['fake-event'], ff_capabilities=['40000 0'], ready=True)
    def prepare(self):
        pass
    def start(self, token):
        self.token = token
    def poll(self):
        return True
    def cancel(self):
        pass
    def finish(self):
        pass


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.directory = self.enterContext(tempfile.TemporaryDirectory())
        self.path = Path(self.directory) / 'run.jsonl'
        self.args = SimpleNamespace(source='fake', capture=str(self.path), capacity=40, strength=20,
                                    gas_axis='z', invert_gas=False, duration=.2, proton='test',
                                    hid_route='unknown', scenario='synthetic')
        self.wheel = FakeWheel()
        self.virtual = FakeVirtual([get_report(0x57, 42)])
        self.selector = FakeSelector()
        self.now = 1_000_000_000
        self.console = self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        self.enterContext(patch('sys.stdin.isatty', return_value=False))
        self.enterContext(patch.object(runner, 'virtual_nodes', return_value=set()))
        self.enterContext(patch.object(runner, 'bindings', return_value=[]))
        self.binder = FakeBinding()
        self.enterContext(patch.object(runner, 'PIDBinding', return_value=self.binder))
        self.enterContext(patch.object(runner.time, 'monotonic_ns', side_effect=self.clock))

    def clock(self):
        self.now += 1_000_000
        return self.now

    def execute(self, **kwargs):
        return runner.run(self.args, wheel_factory=lambda path: self.wheel,
                          virtual_factory=lambda: self.virtual,
                          selector_factory=lambda: self.selector, **kwargs)

    def assert_closed(self):
        self.assertTrue(self.wheel.closed)
        self.assertTrue(self.virtual.closed)
        self.assertTrue(self.selector.closed)

    def test_normal_loop_replies_and_capture_replays(self):
        result = self.execute()
        self.assert_closed()
        self.assertEqual(result['transport']['reads'], 1)
        reply = next(w for w in self.virtual.writes if struct.unpack_from('=I', w)[0] == 10)
        self.assertEqual(struct.unpack_from('=IIHH', reply), (10, 42, 0, 5))
        self.assertTrue(replay(self.path)['complete'])

    def test_close_open_probe_then_game_and_stop_are_processed_once(self):
        self.virtual.events = [struct.pack('=I', 2), struct.pack('=I', 4),
                               set_report(bytes.fromhex('54010000'), 1, 0),
                               struct.pack('=I', 5), struct.pack('=I', 4),
                               set_report(bytes.fromhex('54010000'), 1, 0),
                               set_report(bytes.fromhex('54010000'), 1, 0),
                               output(bytes.fromhex('5501803e')), output(bytes.fromhex('5a010101')),
                               struct.pack('=I', 3)]
        result = self.execute()
        self.assertEqual(result['counts']['created'], 2)
        self.assertEqual(result['counts']['freed'], 2)
        self.assertEqual(result['transport']['duplicates'], 1)
        self.assertTrue(replay(self.path)['complete'])
        self.assert_closed()

    def test_wheel_frames_are_sent_with_original_report_bytes(self):
        states = [[0, 200, 30, -1, -1, [288, 704]], [16383, 0, 255, 1, 1, []]]
        def read_frames():
            if self.wheel.frame_times_ns:
                return []
            self.wheel.frame_times_ns = [self.now-2_000_000, self.now-1_000_000]
            return states
        self.wheel.read_frames = read_frames
        self.execute()
        from dfgt_bridge.input.virtual_g29 import input_event, report
        for state in states:
            self.assertIn(input_event(report(state)), self.virtual.writes)
        self.assertTrue(replay(self.path)['complete'])

    def test_capture_existing_file_does_not_create_virtual_device(self):
        self.path.write_text('preserved')
        factory = unittest.mock.Mock()
        with self.assertRaises(FileExistsError):
            runner.run(self.args, wheel_factory=lambda path: self.wheel, virtual_factory=factory)
        factory.assert_not_called()
        self.assertTrue(self.wheel.closed)
        self.assertEqual(self.path.read_text(), 'preserved')

    def test_create_failure_closes_input_and_capture(self):
        with self.assertRaises(OSError):
            runner.run(self.args, wheel_factory=lambda path: self.wheel,
                       virtual_factory=unittest.mock.Mock(side_effect=OSError('create')))
        self.assertTrue(self.wheel.closed)
        self.assertFalse(replay(self.path)['complete'])

    def test_input_read_failure_closes_everything_and_marks_prefix_incomplete(self):
        self.wheel.read_frames = unittest.mock.Mock(side_effect=EOFError('unplug'))
        with self.assertRaises(EOFError):
            self.execute()
        self.assert_closed()
        self.assertFalse(replay(self.path)['complete'])

    def test_response_write_failure_is_recorded_and_cleans_up(self):
        original = self.virtual.write
        def write(data):
            if struct.unpack_from('=I', data)[0] == 10:
                raise OSError('reply failed')
            original(data)
        self.virtual.write = write
        with self.assertRaises(OSError):
            self.execute()
        self.assert_closed()
        result = replay(self.path)
        self.assertFalse(result['complete'])
        self.assertEqual(result['delivery_errors'], 1)

    def test_capture_disk_failure_still_closes_all_resources(self):
        with patch('dfgt_bridge.capture.CaptureWriter.append', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.execute()
        self.assertTrue(self.wheel.closed)

    def test_renderer_exception_closes_devices(self):
        with patch('dfgt_bridge.ffb.effects.PIDSteeringBridge.components', side_effect=ValueError('bad renderer')):
            with self.assertRaises(ValueError):
                self.execute()
        self.assert_closed()

    def test_high_rate_traffic_has_bounded_batches_and_throttled_diagnostics(self):
        self.args.duration = 2.2
        self.virtual.events = [get_report(0x57, i) for i in range(1000)]
        summary = self.execute()
        self.assertEqual(summary['transport']['reads'], 1000)
        self.assertLess(len(self.console.getvalue().splitlines()), 10)
        self.assertTrue(replay(self.path)['complete'])

    def test_stop_request_exits_cleanly_without_waiting_for_duration(self):
        summary = self.execute(stop={'reason': 'SIGTERM'})
        self.assertEqual(summary['counts'].get('uhid', 0), 0)
        self.assert_closed()
        self.assertTrue(replay(self.path)['complete'])

    def test_kernel_stop_does_not_process_other_ready_input_after_shutdown(self):
        self.virtual.events = [struct.pack('=I', 3)]
        self.wheel.read_frames = unittest.mock.Mock(side_effect=AssertionError('input after STOP'))
        self.execute()
        self.wheel.read_frames.assert_not_called()
        self.assertTrue(replay(self.path)['complete'])

    def test_telemetry_distinguishes_ignored_proprietary_commands_and_pedal_bytes(self):
        self.args.gas_axis = 'y'
        self.wheel.state = lambda: [8192, 128, 64, 0, 0, []]
        self.virtual.events = [output(bytes.fromhex(p)) for p in (
            'f5000000000000', '0d000000000000', 'f8120000000000')]
        self.execute()
        import json
        footer = json.loads(self.path.read_text().splitlines()[-1])
        self.assertEqual(footer['runtime']['report_classification']['proprietary_ignored'], 3)
        self.assertEqual(footer['runtime']['input'], dict(
            abs_x=8192, abs_y=128, abs_z=64, hid_x=32770, hid_z=255, hid_rz=64,
            hid_y=128, gas_axis='y', invert_gas=False))
        self.assertEqual(footer['summary']['counts']['rejected_unsupported_report'], 3)
        self.assertTrue(replay(self.path)['complete'])

    def test_help_and_invalid_motor_options_do_not_open_devices(self):
        with patch.object(runner, 'run', side_effect=AssertionError('must not open')):
            for argv, status in [(['--help'], 0), (['--backend', 'evdev'], 2),
                                 (['--enable-motor'], 2), (['--duration', 'nan'], 2)]:
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exc:
                    runner.main(argv)
                self.assertEqual(exc.exception.code, status)

    def test_main_installs_signal_handlers_and_restores_them(self):
        handlers = {}
        def install(number, handler):
            old = handlers.get(number, signal.SIG_DFL)
            handlers[number] = handler
            return old
        def run(args, *, stop):
            handlers[signal.SIGTERM](signal.SIGTERM, None)
            self.assertEqual(stop['reason'], 'SIGTERM')
        with patch.object(runner.signal, 'signal', side_effect=install), patch.object(runner, 'run', side_effect=run):
            self.assertEqual(runner.main([]), 0)
        self.assertEqual(handlers, {signal.SIGINT: signal.SIG_DFL, signal.SIGTERM: signal.SIG_DFL})
