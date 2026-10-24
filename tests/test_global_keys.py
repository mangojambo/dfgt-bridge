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
        bitmap = sum(1 << v for v in (28, 30, 65, 66, 67, 68)).to_bytes(96, 'little')
        self.enterContext(patch('fcntl.ioctl', return_value=bitmap))

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
        self.assertFalse(keys.devices)

    def test_syn_dropped_fails_to_emergency_instead_of_losing_f9(self):
        keys = self.keys()
        with patch('os.read', return_value=INPUT_EVENT.pack(0, 0, 0, 3, 0)):
            self.assertEqual(keys.read(), [67])
