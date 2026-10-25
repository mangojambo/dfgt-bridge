"""Falhas sintéticas; não abrem dispositivos ou arquivos privados reais."""
import unittest
from test_ffb_audit import ROOT
from dfgt_bridge.startup_errors import operation, io_error


class StartupErrorTests(unittest.TestCase):
    def test_permission_context_redacts_private_directories(self):
        with self.assertRaises(OSError) as raised:
            with operation('launcher.profile.read', '/home/private-account/.config/dfgt-bridge/profiles.json'):
                raise PermissionError(13, 'secret text')
        message = str(raised.exception)
        self.assertIn('launcher.profile.read', message)
        self.assertIn('<home privado>/profiles.json', message)
        self.assertIn('errno=13', message)
        self.assertNotIn('private-account', message)
        self.assertNotIn('secret text', message)

    def test_device_failure_keeps_operation_path_and_errno(self):
        message = io_error(PermissionError(13, 'denied', '/dev/uhid'), 'runner.io')
        self.assertIn('/dev/uhid', message)
        self.assertIn('runner.io', message)
        self.assertIn('errno=13', message)
