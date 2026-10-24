"""STOP de troca de driver só é tolerado durante bootstrap explícito."""
import struct
import unittest

from test_ffb_audit import ROOT
from test_ffb_transport import output, set_report
from dfgt_bridge.passive import PassiveSession


class BindingLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.session = PassiveSession(0)
        self.addCleanup(self.session.core.close)
        self.now = 0

    def apply(self, action, data):
        self.now += 1_000_000
        return self.session.apply(action, data, self.now)

    def uhid(self, raw):
        return self.apply('uhid', dict(raw_hex=raw.hex()))

    def test_bootstrap_stop_clears_probe_effects_and_allows_new_control_ids(self):
        self.apply('binding', dict(ready=False))
        self.uhid(set_report(bytes.fromhex('54010000'), 1, 0))
        self.uhid(output(bytes.fromhex('5501803e')))
        self.uhid(output(bytes.fromhex('5a010101')))
        self.assertNotEqual(self.session.core.output.dev.level, 0)
        self.uhid(struct.pack('=I', 3))
        self.assertFalse(self.session.core.closed)
        self.assertEqual(self.session.core.engine.effects, {})
        self.assertEqual(self.session.core.output.dev.level, 0)
        # Novo driver pode reutilizar ID 1; não é retry do probe anterior.
        self.uhid(set_report(bytes.fromhex('54090000'), 1, 0))
        self.assertEqual(self.session.core.engine.effects[1]['kind'], 9)

    def test_after_ready_stop_again_closes_output_and_transport(self):
        self.apply('binding', dict(ready=False))
        self.uhid(struct.pack('=I', 3))
        self.apply('binding', dict(ready=True))
        self.uhid(struct.pack('=I', 3))
        self.assertTrue(self.session.core.closed)
        self.assertTrue(self.session.core.output.closed)
        self.assertEqual(self.session.core.output.dev.level, 0)
