"""ABI e I/O EVDEV inteiramente simulados. Nunca abre um nó real."""
import ctypes
import os
import struct
import unittest
from unittest.mock import patch

from test_ffb_audit import ROOT
from dfgt_bridge.backends.evdev import Effect, EvdevBackend
from dfgt_bridge.ffb.safety import ConstantForce


class EvdevBackendTests(unittest.TestCase):
    def setUp(self):
        self.identity = (3, 0x046d, 0xc29a, 0x100)
        self.features = {0x52, 0x60, 0x61}
        self.uploads = []
        self.open = self.enterContext(patch('os.open', return_value=73))
        self.closed = self.enterContext(patch('os.close'))
        self.lock = self.enterContext(patch('fcntl.flock'))
        self.io = self.enterContext(patch('fcntl.ioctl', side_effect=self.ioctl))
        self.writes = self.enterContext(patch('os.write', side_effect=lambda fd, data: len(data)))
        self.enterContext(patch('platform.machine', return_value='x86_64'))

    def ioctl(self, fd, command, value, *args):
        if command == 0x80084502:
            return struct.pack('=4H', *self.identity)
        if command == 0x80104535:
            return sum(1 << v for v in self.features).to_bytes(16, 'little')
        if command == 0x80044584:
            return struct.pack('=I', 16)
        if command == 0x40304580:
            effect = Effect.from_buffer_copy(value)
            self.uploads.append(effect)
            struct.pack_into('=h', value, 2, 3)
            return 0
        if command == 0x40044581:
            self.assertEqual(value, 3)
            return 0
        raise AssertionError(hex(command))

    def backend(self):
        dev = EvdevBackend('fake-dfgt', enable_ffb=True)
        self.addCleanup(dev.close)
        return dev

    def events(self):
        return [struct.unpack('@llHHi', c.args[1])[2:] for c in self.writes.call_args_list]

    def test_opt_in_required_before_any_open(self):
        with self.assertRaises(PermissionError):
            EvdevBackend('fake-dfgt')
        self.open.assert_not_called()

    def test_exact_identity_and_all_required_capabilities(self):
        for vendor, product in ((0x046d, 0xc262), (0x046d, 0xc24f), (0x1234, 0xc29a)):
            self.identity = (3, vendor, product, 0x100)
            with self.assertRaises(ValueError):
                self.backend()
            self.assertEqual(self.events(), [])
        self.identity = (3, 0x046d, 0xc29a, 0x100)
        self.features.remove(0x61)
        with self.assertRaises(ValueError):
            self.backend()
        self.assertEqual(self.events(), [])

    def test_initialization_gain_zero_autocenter_off_and_rw_fd(self):
        dev = self.backend()
        self.assertEqual(self.open.call_args.args[1] & os.O_ACCMODE, os.O_RDWR)
        self.assertEqual(self.events(), [(0x15, 0x60, 0), (0x15, 0x61, 0), (0, 0, 0)])
        self.assertEqual(dev.capabilities(), {0x15: [0x52, 0x60, 0x61]})

    def test_native_ff_effect_layout_signed_constant_deadline_and_reuse(self):
        dev = self.backend()
        self.assertEqual((ctypes.sizeof(Effect), Effect.u.offset), (48, 16))
        self.assertEqual(dev.upload_effect(ConstantForce(1000, -1, 160)), 3)
        dev.write(0x15, 3, 1)
        dev.upload_effect(ConstantForce(-1000, 3, 160))
        self.assertEqual([(e.type, e.id, e.direction, e.replay.first, e.u.constant.level)
                          for e in self.uploads], [(0x52, -1, 0x4000, 160, 1000), (0x52, 3, 0x4000, 160, -1000)])
        self.assertIn((0x15, 0x60, 65535), self.events())

    def test_dedup_upload_keeps_same_slot_and_stop_cuts_gain(self):
        dev = self.backend()
        dev.upload_effect(ConstantForce(1000, -1, 160))
        dev.upload_effect(ConstantForce(1000, 3, 160))
        self.assertEqual((dev.uploads, dev.deduplicated), (1, 1))
        dev.write(0x15, 3, 1)
        dev.write(0x15, 3, 0)
        self.assertEqual(self.events()[-2:], [(0x15, 0x60, 0), (0x15, 3, 0)])

    def test_stale_frame_cannot_upload_or_play(self):
        dev = self.backend()
        dev.freshness_guard = lambda: (_ for _ in ()).throw(TimeoutError('stale'))
        with self.assertRaises(TimeoutError):
            dev.upload_effect(ConstantForce(1000, -1, 160))
        self.assertEqual(self.uploads, [])

    def test_upload_that_finishes_after_deadline_cannot_start_playback(self):
        dev = self.backend()
        calls = 0
        def guard():
            nonlocal calls
            calls += 1
            if calls > 1:
                raise TimeoutError('upload terminou tarde')
        dev.freshness_guard = guard
        with self.assertRaises(TimeoutError):
            dev.upload_effect(ConstantForce(1000, -1, 160))
        self.assertEqual(dev.uploads, 1)
        self.assertNotIn((0x15, 3, 1), self.events())
        self.assertNotIn((0x15, 0x60, 65535), self.events())

    def test_close_erases_and_closes_even_when_unplugged(self):
        dev = self.backend()
        dev.upload_effect(ConstantForce(1000, -1, 160))
        self.writes.side_effect = OSError('unplugged')
        dev.close()
        self.closed.assert_called_with(73)
        self.assertIn(unittest.mock.call(73, 0x40044581, 3), self.io.call_args_list)
        dev.close()

    def test_exclusive_writer_lock_failure_never_sends_force(self):
        self.lock.side_effect = BlockingIOError('busy')
        with self.assertRaises(BlockingIOError):
            self.backend()
        self.assertEqual(self.events(), [])
        self.closed.assert_called_with(73)

    def test_short_write_and_unsupported_abi_fail_closed(self):
        self.writes.side_effect = lambda fd, data: 1
        with self.assertRaises(OSError):
            self.backend()
        self.closed.assert_called_with(73)
        with patch('platform.machine', return_value='aarch64'), self.assertRaises(RuntimeError):
            self.backend()

    def test_invalid_or_unbounded_requests_are_rejected(self):
        dev = self.backend()
        for request in (ConstantForce(1000, -1, 0), ConstantForce(1000, -1, 161),
                        ConstantForce(32768, -1, 160), ConstantForce(1, -1, 160, 0)):
            with self.assertRaises(ValueError):
                dev.upload_effect(request)

    def test_full_scale_signed_levels_and_unity_gain_reach_native_abi(self):
        dev = self.backend()
        for value in (0, 3277, 9830, 16384, 24575, 32767, -32768):
            dev.upload_effect(ConstantForce(value, -1 if dev.slot < 0 else dev.slot, 160))
            dev.write(0x15, dev.slot, 1)
            self.assertEqual(self.uploads[-1].u.constant.level, value)
            self.assertEqual(self.uploads[-1].direction, 0x4000)
        self.assertIn((0x15, 0x60, 65535), self.events())
        self.assertNotIn((0x15, 0x60, 32767), self.events())

    def test_backend_rejects_non_integral_or_overflowed_levels_before_upload(self):
        dev = self.backend()
        for value in (-32769, 32768, 1.5, True, float('nan')):
            with self.subTest(value=value), self.assertRaises(ValueError):
                dev.upload_effect(ConstantForce(value, -1, 160))
        self.assertEqual(self.uploads, [])

    def test_hid_renderer_to_native_evdev_at_100_bypasses_mock_twenty_percent(self):
        from test_ffb_transport import output, set_report
        from dfgt_bridge.passive import PassiveSession
        from dfgt_bridge.supervised import SupervisedPolicy
        session = PassiveSession(0, strength_percent=20)
        self.addCleanup(session.core.close)
        now = 0
        for packet, feature in ((bytes.fromhex('54010000'), True),
                                (bytes.fromhex('5501ff7f'), False),
                                (bytes.fromhex('5a0101ff'), False)):
            now += 25_000_000
            raw = set_report(packet, now, 0) if feature else output(packet)
            result = session.apply('uhid', {'raw_hex': raw.hex()}, now)
            self.assertIsNone(result['error'])
        dev = self.backend()
        policy = SupervisedPolicy(dev, strength=100, maximum=100, slew_per_s=327670)
        self.addCleanup(lambda: policy.apply('close', {}, policy.now_ns))
        for generation in range(1, 9):
            now += 25_000_000
            result = session.apply('tick', {}, now)
            policy.apply('frame', dict(generation=generation, calculated_ns=now,
                                      components=result['components']), now+1_000_000)
        self.assertEqual(result['components']['constant'], 32767)
        self.assertLessEqual(result['final_force'], 6553)
        self.assertEqual(self.uploads[-1].u.constant.level, 32767)
        self.assertEqual(dev.diagnostic()['level_commanded'], 32767)
        self.assertEqual(dev.diagnostic()['gain_commanded'], 65535)
        policy.apply('stop', dict(reason='F9', latch=True), policy.now_ns+1)
        self.assertEqual(dev.diagnostic()['level_commanded'], 0)
        self.assertEqual(dev.diagnostic()['gain_commanded'], 0)
