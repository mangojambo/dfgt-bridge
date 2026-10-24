"""Metadados de propriedade UHID preservam descritor e serial v0.8."""
import unittest
from unittest.mock import patch
from test_ffb_audit import BASE, load_reviewed_definitions
from dfgt_bridge.input.virtual_g29 import create_event


class UHIDOwnershipTests(unittest.TestCase):
    def test_unique_metadata_preserves_descriptor_identity_and_input_layout(self):
        baseline = create_event()
        unique = create_event(phys='dfgt-bridge-test-session')
        self.assertEqual(unique[:132], baseline[:132])
        self.assertEqual(unique[196:], baseline[196:])
        self.assertEqual(unique[132:196].rstrip(b'\0'), b'dfgt-bridge-test-session')
        self.assertEqual(unique[196:260], bytes(64))  # Serial/uniq continua vazio como v0.8.
        self.assertEqual(unique[280:], load_reviewed_definitions(BASE).descriptor())

    def test_virtual_device_writes_its_ownership_token_into_create2(self):
        from dfgt_bridge.uhid_io import VirtualDevice
        writes = []
        with patch('os.open', return_value=71), patch('os.close'), patch('os.write', side_effect=lambda fd, data: writes.append(data) or len(data)):
            with VirtualDevice() as device:
                self.assertEqual(writes[0][132:196].rstrip(b'\0').decode(), device.owner_token)
                self.assertTrue(device.owner_token.startswith('dfgt-bridge-'))

    def test_invalid_ownership_field_is_rejected(self):
        for token in ('x'*64, 'bad\0token', 'não-ascii'):
            with self.assertRaises(ValueError):
                create_event(phys=token)
