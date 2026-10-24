"""Contrato v0.8 e falhas de I/O simuladas; nenhum dispositivo é aberto."""
import ast
import itertools
import os
import struct
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from test_ffb_audit import BASE, load_reviewed_definitions
from dfgt_bridge.input import virtual_g29 as virtual
from dfgt_bridge.input.physical_wheel import INPUT_EVENT, PhysicalWheel
from dfgt_bridge.uhid_io import VirtualDevice


class BaselineInputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.baseline = load_reviewed_definitions(BASE)

    def test_descriptor_identity_and_create_event_match_baseline(self):
        self.assertEqual(virtual.descriptor(), self.baseline.descriptor())
        nodes = [n for n in ast.parse(BASE.read_text()).body
                 if isinstance(n, ast.FunctionDef) and n.name in ('create_device', 'send_input')]
        writes = []
        ns = dict(struct=struct, os=SimpleNamespace(write=lambda fd, data: writes.append(data)),
                  UHID_CREATE2=11, UHID_INPUT2=12, UHID_DATA_MAX=4096,
                  descriptor=self.baseline.descriptor)
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(BASE), 'exec'), ns)
        ns['create_device'](99, virtual.NAME, 0x046d, 0xc24f)
        self.assertEqual(virtual.create_event(), writes.pop())
        packet = virtual.report((8192, 255, 255, 0, 0, set()))
        ns['send_input'](99, packet)
        self.assertEqual(virtual.input_event(packet), writes.pop())

    def test_axes_pedals_hats_options_and_all_buttons(self):
        for x, a, b, hx, hy, axis, invert in itertools.product(
                (-1, 0, 8191, 8192, 16383, 16384), (0, 127, 255), (0, 255),
                (-1, 0, 1), (-1, 0, 1), ('z', 'y', 'both'), (False, True)):
            state = (x, a, b, hx, hy, set(virtual.BUTTON_CODES))
            self.assertEqual(virtual.report(state, axis, invert), self.baseline.report(state, axis, invert))
        for code in [*virtual.BUTTON_CODES, 999]:
            state = (8192, 255, 255, 0, 0, {code})
            self.assertEqual(virtual.report(state), self.baseline.report(state))


class PhysicalInputTests(unittest.TestCase):
    def ioctl(self, fd, request, data):
        if request == 0x80084502:
            return struct.pack('=4H', 3, 0x046d, 0xc29a, 0x100)
        if request == 0x80604518:
            return (1 << 288).to_bytes(96, 'little')
        if request == 0x400445a0:
            return bytes(4)
        code = request - 0x80184540
        return struct.pack('=6i', {0: 8192, 1: 255, 2: 255, 16: -1, 17: 0}[code],
                           0, 16383 if code == 0 else 255, 0, 0, 0)

    def setUp(self):
        self.open = self.enterContext(patch('os.open', return_value=71))
        self.close = self.enterContext(patch('os.close'))
        self.enterContext(patch('fcntl.ioctl', side_effect=self.ioctl))
        self.wheel = PhysicalWheel()
        self.addCleanup(self.wheel.close)

    def event(self, kind, code, value):
        return INPUT_EVENT.pack(0, 0, kind, code, value)

    def test_source_open_is_readonly_and_initial_state_matches_v08(self):
        self.assertEqual(self.open.call_args.args[1] & os.O_ACCMODE, os.O_RDONLY)
        self.assertEqual(self.wheel.state(), (8192, 255, 255, 0, 0, []))

    def test_frames_preserve_mapping_and_ignore_key_repeat(self):
        data = b''.join(self.event(*e) for e in [(3, 0, 1), (3, 1, 42), (3, 2, 61),
              (3, 16, 1), (3, 17, -1), (1, 288, 1), (1, 289, 2), (0, 0, 0)])
        with patch('os.read', return_value=data):
            self.assertEqual(self.wheel.read_frames(), [(1, 42, 61, 1, -1, [288])])

    def test_syn_dropped_discards_until_sync_then_resynchronizes(self):
        data = b''.join(self.event(*e) for e in [(0, 3, 0), (3, 0, 999), (1, 289, 1), (0, 0, 0)])
        with patch('os.read', return_value=data):
            self.assertEqual(self.wheel.read_frames(), [(8192, 255, 255, -1, 0, [288])])
        self.assertEqual(self.wheel.resyncs, 1)

    def test_partial_and_eof_raise_and_eagain_is_empty(self):
        for value in (b'bad', b''):
            with patch('os.read', return_value=value), self.assertRaises((OSError, EOFError)):
                self.wheel.read_frames()
        with patch('os.read', side_effect=BlockingIOError):
            self.assertEqual(self.wheel.read_frames(), [])

    def test_bad_identity_or_range_closes_fd(self):
        with patch('fcntl.ioctl', return_value=bytes(8)), self.assertRaises(ValueError):
            PhysicalWheel()
        self.close.assert_called_with(71)
        def wrong_range(fd, request, data):
            return self.ioctl(fd, request, data) if request == 0x80084502 else bytes(24)
        with patch('fcntl.ioctl', side_effect=wrong_range), self.assertRaises(ValueError):
            PhysicalWheel()


class VirtualIOTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('os.open', return_value=72))
        self.closed = self.enterContext(patch('os.close'))
        self.writes = self.enterContext(patch('os.write', side_effect=lambda fd, data: len(data)))

    def test_create_input_reply_destroy_are_whole_writes(self):
        with VirtualDevice() as dev:
            dev.write(virtual.input_event(virtual.report((0, 255, 255, 0, 0, []))))
            dev.write(struct.pack('=IIH', 14, 42, 0))
        self.assertEqual([struct.unpack_from('=I', c.args[1])[0] for c in self.writes.call_args_list],
                         [11, 12, 14, 1])
        self.closed.assert_called_once_with(72)

    def test_partial_write_does_not_send_tail_or_reapply(self):
        dev = VirtualDevice()
        self.writes.side_effect = lambda fd, data: 1
        with self.assertRaises(OSError):
            dev.write(b'1234')
        self.writes.assert_called_with(72, b'1234')
        self.writes.side_effect = lambda fd, data: len(data)
        dev.close()

    def test_create_failure_and_destroy_failure_still_close_fd(self):
        self.writes.side_effect = OSError('failure')
        with self.assertRaises(OSError):
            VirtualDevice()
        self.closed.assert_called_with(72)
        self.writes.side_effect = lambda fd, data: len(data)
        dev = VirtualDevice()
        self.writes.side_effect = OSError('failure')
        with self.assertRaises(OSError):
            dev.close()
        self.assertEqual(dev.fd, -1)

    def test_eagain_retries_whole_event_and_timeout_is_bounded(self):
        dev = VirtualDevice()
        self.writes.side_effect = [BlockingIOError(), 4]
        with patch('select.select'):
            dev.write(b'1234')
        self.assertEqual([c.args[1] for c in self.writes.call_args_list[-2:]], [b'1234'] * 2)
        self.writes.side_effect = BlockingIOError()
        with patch('time.monotonic', side_effect=[0, 1]), self.assertRaises(TimeoutError):
            dev.write(b'1234')
        self.writes.side_effect = lambda fd, data: len(data)
        dev.close()
