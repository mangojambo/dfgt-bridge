"""Synthetic transition vectors: deterministic clock and MockBackend only."""
import copy
import json
import struct
from types import SimpleNamespace
import unittest

from test_ffb_audit import ROOT
from dfgt_bridge.backends.mock import MockBackend
from dfgt_bridge.ffb.effects import OUTPUT_COMPONENTS
from dfgt_bridge.supervised import SupervisedPolicy
from dfgt_bridge.passive import PassiveSession
from dfgt_bridge.physical_worker import PhysicalClient, MAX_STATUS_PACKET
from dfgt_bridge.capture import canonical
from test_ffb_transport import output, set_report


class LatchDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.policy = SupervisedPolicy(MockBackend(), require_game=True)
        self.policy.apply('game', dict(running=True, identity=[42, 123]), 0)
        self.addCleanup(lambda: self.policy.apply('close', {}, self.policy.now_ns)
                        if not self.policy.closed else None)

    def frame(self, generation, calculated_ms, received_ms=None, epoch=0, level=1000, **extra):
        return self.policy.apply('frame', dict(
            generation=generation, calculated_ns=calculated_ms*1_000_000,
            stop_epoch=epoch, components={k: level if k == 'constant' else 0
                                         for k in OUTPUT_COMPONENTS}, **extra),
            (calculated_ms if received_ms is None else received_ms)*1_000_000)

    def test_expired_pre_stop_packet_cannot_latch_after_new_epoch_was_accepted(self):
        self.frame(1, 1)
        self.policy.apply('stop', dict(reason='reset', epoch=1), 2_000_000)
        self.frame(3, 90, epoch=1)
        before = (self.policy.output.writes, self.policy.output.play_requests)
        value = self.frame(2, 3, received_ms=110, epoch=0)
        self.assertFalse(value['latched'])
        self.assertEqual(value['last_generation'], 3)
        self.assertEqual(value['age_ns'], 20_000_000)
        self.assertEqual(value['rejected'], 1)
        self.assertEqual((self.policy.output.writes, self.policy.output.play_requests), before)
        self.assertEqual(value['safety_diagnostic']['last_rejection']['reason'], 'discarded_pre_stop_frame')
        self.assertNotEqual(self.frame(4, 115, epoch=1)['evdev_level'], 0)

    def test_1947_overtaken_frames_after_stop_wait_without_permanent_latch(self):
        self.frame(1, 1)
        for generation in range(2, 1949):
            stamp = generation*5_000_000
            self.policy.apply('stop', dict(reason='reset', epoch=generation), stamp)
            value = self.frame(generation, generation*5, epoch=generation-1)
        self.assertFalse(value['latched'])
        self.assertEqual(value['evdev_level'], 0)
        self.assertEqual(value['rejected'], 1947)
        self.assertEqual(value['safety_events'], 0)
        self.assertEqual(value['zero_reason'], 'discarded_pre_stop_frame')
        detail = value['safety_diagnostic']
        self.assertEqual(detail['rejected_by_reason'], {'discarded_pre_stop_frame': 1947})
        self.assertIsNone(detail['first_latch'])
        first = value['transient_recovery']['last_timeout']
        self.assertEqual(first['timestamp_ns'], 105_000_000)
        self.assertEqual(first['age_ns'], 104_000_000)
        self.assertEqual(first['previous_stop_reason'], 'reset')
        self.assertEqual(first['last_generation'], 1)

    def test_first_latch_survives_discard_strength_repeated_stop_and_close(self):
        self.frame(1, 1, last_pid_command=dict(packet_hex='5501e803', calculated_ns=1_000_000))
        self.policy.apply('stop', dict(reason='F9', latch=True, epoch=2), 2_000_000)
        first = copy.deepcopy(self.policy.snapshot()['safety_diagnostic']['first_latch'])
        self.frame(2, 3, epoch=1)
        self.policy.apply('strength', dict(percent=0), 4_000_000)
        self.policy.apply('stop', dict(reason='terminal_emergency', latch=True), 5_000_000)
        value = self.policy.apply('close', {}, 6_000_000)
        self.assertEqual(value['zero_reason'], 'F9')
        self.assertEqual(value['safety_diagnostic']['first_latch'], first)
        self.assertEqual(first['last_pid_command']['packet_hex'], '5501e803')
        self.assertEqual(first['stop_epoch'], 2)
        self.assertEqual(first['minimum_epoch'], 2)
        self.assertEqual(first['category'], 'operator')
        self.assertEqual(value['safety_events'], 1)

    def test_latch_alone_does_not_reject_valid_current_epoch_calculations(self):
        self.frame(1, 1)
        self.policy.apply('stop', dict(reason='F9', latch=True), 2_000_000)
        for generation in range(2, 202):
            value = self.frame(generation, generation*5)
        self.assertEqual(value['rejected'], 0)
        self.assertEqual(value['accepted'], 201)
        self.assertEqual(value['renderer_total'], 1000)
        self.assertEqual(value['evdev_level'], 0)
        self.assertEqual(value['zero_reason'], 'F9')

    def test_game_rediscovery_and_new_generation_do_not_rearm(self):
        self.frame(1, 1)
        self.policy.apply('game', dict(running=False, identity=None), 2_000_000)
        self.policy.apply('game', dict(running=True, identity=[42, 456]), 3_000_000)
        value = self.frame(2, 4)
        self.assertTrue(value['latched'])
        self.assertEqual(value['zero_reason'], 'game_exit')
        first = value['safety_diagnostic']['first_latch']
        self.assertEqual(first['previous_game_identity'], [42, 123])
        self.assertIsNone(first['game_identity'])

    def test_explicit_arm_requires_fresh_generation_and_current_epoch(self):
        self.frame(1, 1)
        self.policy.apply('stop', dict(reason='F9', latch=True, epoch=2), 2_000_000)
        first = copy.deepcopy(self.policy.snapshot()['safety_diagnostic']['first_latch'])
        self.policy.apply('arm', dict(requested_ns=3_000_000), 3_000_000)
        self.assertEqual(self.frame(2, 2, received_ms=4, epoch=2)['evdev_level'], 0)
        self.assertEqual(self.frame(3, 5, epoch=1)['evdev_level'], 0)
        self.assertNotEqual(self.frame(4, 6, epoch=2)['evdev_level'], 0)
        self.policy.apply('stop', dict(reason='keyboard_loss', latch=True), 7_000_000)
        detail = self.policy.snapshot()['safety_diagnostic']
        self.assertEqual(detail['first_latch'], first)
        self.assertEqual(detail['last_latch']['trigger'], 'keyboard_loss')
        self.assertEqual(self.policy.events, 2)

    def test_current_epoch_invalid_and_nonadvancing_calculations_still_latch(self):
        for calculated, expected in ((1, 'calculation_clock_not_advancing'),
                                    (3, 'invalid_or_stale_calculation')):
            with self.subTest(calculated=calculated):
                policy = SupervisedPolicy(MockBackend())
                components = dict.fromkeys(OUTPUT_COMPONENTS, 1)
                policy.apply('frame', dict(generation=1, calculated_ns=1,
                             components=components), 1)
                value = policy.apply('frame', dict(generation=2, calculated_ns=calculated,
                                     components=components), 2)
                self.assertTrue(value['latched'])
                self.assertEqual(value['zero_reason'], expected)
                self.assertEqual(value['safety_diagnostic']['first_latch']['trigger'], expected)
                policy.apply('close', {}, 2)

    def test_watchdog_boundary_unchanged_even_for_old_epoch_packet(self):
        self.frame(1, 1)
        self.policy.apply('stop', dict(reason='pause', epoch=1), 2_000_000)
        self.assertFalse(self.frame(2, 100, epoch=0)['latched'])
        value = self.frame(3, 101, epoch=0)
        self.assertFalse(value['latched'])
        self.assertTrue(value['transient_recovery']['waiting'])
        self.assertEqual(value['transient_recovery']['last_timeout']['age_ns'], 100_000_000)

    def test_real_stop_then_105ms_gap_recovers_only_with_post_timeout_frame(self):
        self.frame(1, 1)
        self.policy.apply('stop', dict(reason='PID_stop_reset_disable_or_close', epoch=1), 2_000_000)
        value = self.policy.apply('poll', {}, 106_196_896)
        self.assertFalse(value['latched'])
        self.assertEqual(value['transient_recovery']['last_timeout']['age_ns'], 105_196_896)
        self.assertEqual(value['evdev_level'], 0)
        plays = self.policy.output.play_requests
        # A new generation alone is insufficient; this packet was calculated
        # before timeout. Even though <100 ms old, it cannot resume output.
        self.assertEqual(self.frame(2, 100, received_ms=107, epoch=1)['evdev_level'], 0)
        self.assertEqual(self.frame(3, 108, epoch=0)['evdev_level'], 0)
        self.assertEqual(self.policy.output.play_requests, plays)
        value = self.frame(4, 109, epoch=1)
        self.assertNotEqual(value['evdev_level'], 0)
        self.assertFalse(value['transient_recovery']['waiting'])
        self.assertEqual(value['transient_recovery']['recoveries'], 1)

    def test_late_stop_takes_priority_over_active_watchdog_boundary(self):
        self.frame(1, 1)
        self.assertTrue(self.policy.output.playing)
        value = self.policy.apply('stop', dict(reason='PID_stop_reset_disable_or_close', epoch=1), 106_000_000)
        self.assertEqual(value['evdev_level'], 0)
        self.assertFalse(value['latched'])
        self.assertTrue(value['transient_recovery']['waiting'])
        self.assertNotEqual(self.frame(2, 107, epoch=1)['evdev_level'], 0)

    def test_emergency_during_idle_wait_still_requires_explicit_arm(self):
        self.frame(1, 1, level=0)
        self.policy.apply('poll', {}, 106_000_000)
        self.policy.apply('stop', dict(reason='F9', latch=True), 107_000_000)
        value = self.frame(2, 108)
        self.assertTrue(value['latched'])
        self.assertEqual(value['evdev_level'], 0)
        self.assertEqual(value['safety_diagnostic']['first_latch']['trigger'], 'F9')

    def test_idle_wait_never_softens_invalid_numbers_or_future_clock(self):
        self.frame(1, 1, level=0)
        self.policy.apply('poll', {}, 106_000_000)
        data = dict(generation=2, calculated_ns=107_000_000,
                    components=dict.fromkeys(OUTPUT_COMPONENTS, float('nan')))
        value = self.policy.apply('frame', data, 107_000_000)
        self.assertTrue(value['latched'])
        self.assertEqual(value['evdev_level'], 0)

    def test_no_writes_or_repeated_events_during_multi_second_idle_gap(self):
        self.frame(1, 1, level=0)
        for t in range(102, 2103, 10):
            value = self.policy.apply('poll', {}, t*1_000_000)
        self.assertEqual(value['transient_recovery']['timeouts'], 1)
        self.assertEqual(value['uploads'], 0)
        self.assertEqual(value['plays'], 0)
        self.assertFalse(value['latched'])
        self.assertNotEqual(self.frame(2, 2104)['evdev_level'], 0)

    def test_full_pool_and_two_latches_fit_bounded_status_with_io_evidence(self):
        self.frame(1, 1)
        self.policy.frame['effects'] = [[i, 1, True, 1.0] for i in range(1, 41)]
        self.policy.frame['game_force'] = dict(pid_gain=1, constant_levels=[[i, -32768] for i in range(1, 41)])
        self.policy.apply('stop', dict(reason='F9', latch=True), 2_000_000)
        self.policy.apply('arm', dict(requested_ns=3_000_000), 3_000_000)
        self.policy.apply('stop', dict(reason='keyboard_loss', latch=True), 4_000_000)
        value = dict(self.policy.snapshot(), ready=True, pid=99999, at_ns=4_000_000,
            keyboard_errors=['x'*160]*3, game_identity=[99999, 123456789],
            first_latch_io=dict(pid=99999, state='handling_frame', stop_epoch=12345,
                emergency_epoch=12345, keyboards=3, keyboard_errors=['x'*160]*3,
                io_before=dict(io_calls=99999, gain_commanded=65535, level_commanded=32767),
                io_after=dict(io_calls=99999, gain_commanded=0, level_commanded=0)))
        self.assertLessEqual(len(canonical(value).encode()), MAX_STATUS_PACKET)


class RaceTransitionTests(unittest.TestCase):
    """Actual PID decoder/renderer -> bounded producer -> mock supervisor."""
    def setUp(self):
        self.now = 0
        self.session = PassiveSession(0)
        self.policy = SupervisedPolicy(MockBackend(clock=lambda: self.now/1e9), require_game=True)
        self.policy.apply('game', dict(running=True, identity=[42, 123]), 0)
        self.client = PhysicalClient.__new__(PhysicalClient)
        self.client.generation = self.client.coalesced = 0
        self.client.latest = self.client.last_pid_command = None
        self.client.last_zero = True
        self.client.stop_epoch = SimpleNamespace(value=0)
        self.client.service = lambda: None
        self.addCleanup(self.session.core.close)
        self.addCleanup(lambda: self.policy.apply('close', {}, self.now))

    def step(self, packet=None, feature=False):
        self.now += 25_000_000
        if packet is None:
            result = self.session.apply('tick', {}, self.now)
        else:
            event = set_report(packet, self.client.generation+1, 0) if feature else output(packet)
            result = self.session.apply('uhid', dict(raw_hex=event.hex()), self.now)
            self.assertIsNone(result['error'])
        self.client.publish(result, self.session)
        frame = json.loads(self.client.latest)
        if self.client.stop_epoch.value > self.policy.minimum_epoch:
            self.policy.apply('stop', dict(reason='PID_stop_reset_disable_or_close',
                              epoch=self.client.stop_epoch.value), self.now)
        return self.policy.apply('frame', frame, self.now)

    def new_constant(self):
        self.step(bytes.fromhex('54010000'), feature=True)
        block = self.session.core.engine.last_block
        self.step(bytes((0x51, block, 1)) + struct.pack('<HHHHBBBHH',
                  0xffff, 0, 0, 0, 100, 255, 4, 9000, 0))
        self.step(bytes((0x55, block)) + struct.pack('<h', 8000))
        value = self.step(bytes((0x5a, block, 1, 1)))
        self.assertNotEqual(value['evdev_level'], 0)
        return block

    def loading(self):
        for _ in range(80):  # Two seconds without new host reports.
            value = self.step()
            self.assertFalse(value['latched'])
            self.assertEqual(value['evdev_level'], 0)

    def test_two_races_menu_loading_pause_disable_free_reset(self):
        block = self.new_constant()
        for _ in range(40):
            self.assertNotEqual(self.step()['evdev_level'], 0)
        self.step(bytes.fromhex('5005'))  # Pause, preserve effect state.
        self.loading()
        self.assertNotEqual(self.step(bytes.fromhex('5006'))['evdev_level'], 0)
        self.step(bytes.fromhex('5002'))  # Disable actuators.
        self.loading()
        self.assertNotEqual(self.step(bytes.fromhex('5001'))['evdev_level'], 0)
        self.step(bytes((0x5a, block, 3, 0)))
        self.loading()
        self.step(bytes((0x5b, block)))
        self.step(bytes.fromhex('5003'))  # Stop All.
        self.step(bytes.fromhex('5004'))  # Reset and rebuild for race two.
        self.loading()
        self.new_constant()
        self.assertFalse(self.policy.latched)
        self.assertEqual(self.policy.events, 0)
        self.assertGreater(self.policy.minimum_epoch, 0)

    def test_delayed_previous_race_frame_cannot_latch_or_restore_old_force(self):
        self.new_constant()
        old = json.loads(self.client.latest)
        self.step(bytes.fromhex('5004'))
        self.loading()
        before = (self.policy.output.writes, self.policy.output.play_requests, self.policy.last_valid_ns)
        value = self.policy.apply('frame', old, self.now)
        self.assertFalse(value['latched'])
        self.assertEqual(value['evdev_level'], 0)
        self.assertEqual((self.policy.output.writes, self.policy.output.play_requests,
                          self.policy.last_valid_ns), before)
        self.assertEqual(value['rejected'], 1)
        self.new_constant()
        self.assertNotEqual(self.policy.output.last_sent, 0)

    def test_repeated_stop_start_across_loading_gaps_longer_than_watchdog(self):
        for _ in range(3):
            block = self.new_constant()
            self.step(bytes((0x5a, block, 3, 0)))
            old = json.loads(self.client.latest)
            # A distinct in-flight generation calculated before the stall.
            self.client.generation += 1
            old['generation'] = self.client.generation
            self.now += 500_000_000
            value = self.policy.apply('poll', {}, self.now)
            self.assertFalse(value['latched'])
            self.assertEqual(value['evdev_level'], 0)
            before = self.policy.output.play_requests
            self.assertEqual(self.policy.apply('frame', old, self.now)['evdev_level'], 0)
            self.assertEqual(self.policy.output.play_requests, before)
            self.assertNotEqual(self.step(bytes((0x5a, block, 1, 1)))['evdev_level'], 0)
            self.step(bytes.fromhex('5004'))
        self.assertEqual(self.policy.idle_timeouts, 3)
        self.assertEqual(self.policy.idle_recoveries, 3)
        self.assertEqual(self.policy.events, 0)

    def test_redundant_stop_reports_do_not_repeatedly_invalidate_fresh_frames(self):
        block = self.new_constant()
        packet = bytes((0x5a, block, 3, 0))
        self.step(packet)
        epoch = self.client.stop_epoch.value
        for _ in range(40):
            value = self.step(packet)
            self.assertEqual(self.client.stop_epoch.value, epoch)
            self.assertEqual(value['evdev_level'], 0)
            self.assertFalse(value['latched'])
        self.assertNotEqual(self.step(bytes((0x5a, block, 1, 1)))['evdev_level'], 0)
