"""Teclas globais com EVDEV de teclado simulado, sem grabs ou motor."""
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from test_ffb_audit import ROOT
from dfgt_bridge.input.global_keys import GlobalKeys
from dfgt_bridge.input.physical_wheel import INPUT_EVENT


class GlobalKeysTests(unittest.TestCase):
    def setUp(self):
        self.open = self.enterContext(patch('os.open', return_value=81))
        self.close = self.enterContext(patch('os.close'))
        self.enterContext(patch('os.fstat', return_value=SimpleNamespace(st_rdev=100)))
        bitmap = sum(1 << v for v in (28, 30, 63, 64, 65, 66, 67, 68)).to_bytes(96, 'little')
        self.ioctl = self.enterContext(patch('fcntl.ioctl', side_effect=lambda fd, op, data:
            bytes(96) if op == 0x80604518 else bitmap))

    def keys(self):
        keys = GlobalKeys(enable_ffb=True, path='fake-keyboard')
        self.addCleanup(keys.close)
        return keys

    def test_default_cannot_open_any_keyboard(self):
        with self.assertRaises(PermissionError):
            GlobalKeys(path='fake-keyboard')
        self.open.assert_not_called()

    def test_global_readonly_keypress_without_autorepeat_or_release(self):
        keys = self.keys()
        self.assertEqual(self.open.call_args.args[1] & os.O_ACCMODE, os.O_RDONLY)
        data = b''.join(INPUT_EVENT.pack(0, 0, 1, k, v) for k, v in (
            (65, 1), (66, 1), (67, 1), (68, 1), (66, 2), (67, 0), (30, 1)))
        with patch('os.read', return_value=data):
            self.assertEqual(keys.read(), [65, 66, 67, 68])
        self.assertEqual(keys.stop_reasons, ['F9'])

    def test_missing_permissions_is_reported_without_force_or_grab(self):
        self.open.side_effect = PermissionError('no permission')
        keys = self.keys()
        self.assertEqual(keys.devices, {})
        self.assertIn('no permission', keys.errors[0])

    def test_non_keyboard_is_closed_and_ignored(self):
        with patch('fcntl.ioctl', return_value=bytes(96)):
            keys = self.keys()
        self.assertEqual(keys.devices, {})
        self.close.assert_called_with(81)

    def test_eagain_is_empty_and_unplug_triggers_emergency(self):
        keys = self.keys()
        with patch('os.read', side_effect=BlockingIOError()):
            self.assertEqual(keys.read(), [])
        with patch('os.read', return_value=b''):
            self.assertEqual(keys.read(), [67])
        self.assertEqual(keys.stop_reasons, ['keyboard_loss'])
        self.assertFalse(keys.devices)

    def test_syn_dropped_fails_to_emergency_instead_of_losing_f9(self):
        keys = self.keys()
        with patch('os.read', return_value=INPUT_EVENT.pack(0, 0, 0, 3, 0)):
            self.assertEqual(keys.read(), [67])
        self.assertEqual(keys.stop_reasons, ['keyboard_loss'])
        self.assertIn('SYN_DROPPED', keys.errors[-1])

    def test_f5_f6_ignore_autorepeat_duplicate_down_and_release(self):
        keys = self.keys()
        def events(pairs):
            return b''.join(INPUT_EVENT.pack(0, 0, 1, k, v) for k, v in pairs)
        with patch('os.read', return_value=events(((63, 1), (63, 2), (63, 1), (64, 1), (64, 2)))):
            self.assertEqual(keys.read(), [63, 64])
        with patch('os.read', return_value=events(((63, 0), (64, 0), (64, 1), (63, 1)))):
            self.assertEqual(keys.read(), [64, 63])

    def test_modified_f5_f6_leave_system_shortcuts_alone_and_never_mask_f9(self):
        keys = self.keys()
        for modifier in (29, 97, 42, 54, 56, 100, 125, 126):
            data = b''.join(INPUT_EVENT.pack(0, 0, 1, k, v) for k, v in (
                (modifier, 1), (63, 1), (64, 1), (67, 1),
                (63, 0), (64, 0), (modifier, 0), (63, 1), (63, 0)))
            with patch('os.read', return_value=data):
                self.assertEqual(keys.read(), [67, 63])

    def test_modifier_held_at_start_is_read_without_grabbing_keyboard(self):
        original = self.ioctl.side_effect
        held = (1 << 125).to_bytes(96, 'little')
        self.ioctl.side_effect = lambda fd, op, data: held if op == 0x80604518 else original(fd, op, data)
        keys = self.keys()
        data = INPUT_EVENT.pack(0, 0, 1, 64, 1)
        with patch('os.read', return_value=data):
            self.assertEqual(keys.read(), [])
        self.assertFalse(any(call.args[1] == 0x40044590 for call in self.ioctl.call_args_list))  # No EVIOCGRAB.
