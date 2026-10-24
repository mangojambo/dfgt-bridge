"""Políticas adicionais estritamente mock para enumeração e corridas longas."""
import struct
import unittest

from test_ffb_audit import FakeDevice
import test_ffb_integration as integration
from test_ffb_transport import get_report
from dfgt_bridge.ffb.renderer import MotionState
from dfgt_bridge.ffb.safety import ConstantOutput
from dfgt_bridge.ffb.session import OfflineSession


class PassivePolicyTests(unittest.TestCase):
    def setUp(self):
        self.now = 0.0
        self.session = OfflineSession(lambda: self.now, close_terminates=False, test_limit_s=None)
        self.addCleanup(self.session.close)

    def test_close_clears_effects_and_output_but_open_can_enumerate_again(self):
        helper = integration.IntegrationTests()
        helper.session, helper.requests = self.session, iter(range(1, 100))
        helper.constant()
        self.assertNotEqual(self.session.output.dev.level, 0)
        self.session.handle(struct.pack('=I', 5))
        self.assertFalse(self.session.closed)
        self.assertEqual(self.session.engine.effects, {})
        self.assertEqual(self.session.output.dev.level, 0)
        self.session.handle(struct.pack('=I', 4))
        self.assertEqual(self.session.handle(get_report(0x57, 1, 0))[0][8:10], b'\0\0')
        helper.constant()
        self.now = .03
        self.session.tick()
        self.assertNotEqual(self.session.output.dev.level, 0)
        self.session.handle(struct.pack('=I', 3))
        self.assertTrue(self.session.closed)

    def test_only_mock_can_disable_total_duration_limit(self):
        with self.assertRaises(ValueError):
            ConstantOutput(FakeDevice(), test_limit_s=None)
        helper = integration.IntegrationTests()
        helper.session, helper.requests = self.session, iter(range(1, 100))
        helper.constant()
        self.now = 3600
        self.session.tick()
        self.assertNotEqual(self.session.output.dev.level, 0)
        self.assertFalse(self.session.output.expired)

    def test_latest_external_motion_is_reused_for_uhid_events(self):
        motion = MotionState(.5, 2, 3)
        self.session.tick(12287, motion=motion)
        estimator = self.session.engine.motion_estimator
        # O teste só observa a chamada; não recalcula derivadas a cada GET.
        from unittest.mock import patch
        with patch.object(estimator, 'sample', side_effect=AssertionError('synthetic motion')):
            self.session.handle(get_report(0x57, 1, 0))
        self.assertIs(self.session.motion, motion)
