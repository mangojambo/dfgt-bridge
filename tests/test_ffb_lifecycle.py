"""Sequências PID sintéticas; somente estado em memória e relógio simulado."""
import struct
import unittest

from test_ffb_audit import M


class LifecycleVectors(unittest.TestCase):
    def setUp(self):
        self.now = 0.0
        self.bridge = M.PIDSteeringBridge(clock=lambda: self.now)
        self.bridge.arm()

    def create(self, kind=1):
        self.bridge.set_report(bytes((0x54, kind, 0, 0)), 0x54)
        return self.bridge.last_block

    def configure(self, block, duration=0xffff, delay=0):
        self.bridge.seen(bytes((0x51, block, 1)) + struct.pack(
            '<HHHHBBBHH', duration, 0, 0, delay, 100, 255, 4, 9000, 0))

    def constant(self, level=16000, duration=0xffff, delay=0, loops=1):
        block = self.create()
        self.configure(block, duration, delay)
        self.bridge.seen(bytes((0x55, block)) + struct.pack('<h', level))
        self.bridge.seen(bytes((0x5a, block, 1, loops)))
        return block


class PoolTests(LifecycleVectors):
    def test_full_pool_preserves_all_effects_and_identifiers(self):
        ids = [self.create() for _ in range(40)]
        snapshot = dict(self.bridge.effects)
        self.assertEqual(ids, list(range(1, 41)))
        self.assertEqual(self.create(), 0)
        self.assertEqual(self.bridge.get_report(0x56), bytes.fromhex('5600020000'))
        self.assertEqual(self.bridge.effects, snapshot)

    def test_free_reuses_lowest_id_without_moving_other_blocks(self):
        self.create(); second = self.create(); self.create()
        third = self.bridge.effects[3]
        self.bridge.seen(bytes((0x5b, second)))
        self.assertEqual(self.create(), second)
        self.assertIs(self.bridge.effects[3], third)

    def test_reset_removes_all_blocks_and_pending_allocation(self):
        self.constant()
        self.bridge.seen(bytes((0x50, 4)))
        self.assertEqual(self.bridge.effects, {})
        self.assertEqual(self.bridge.get_report(0x56), bytes.fromhex('560003000a'))
        self.assertEqual(self.create(), 1)

    def test_configurable_capacity_and_ram_accounting(self):
        self.bridge = M.PIDSteeringBridge(clock=lambda: self.now, capacity=2)
        self.bridge.arm()
        self.assertEqual(self.bridge.get_report(0x57), bytes.fromhex('5780000201'))
        self.create()
        self.assertEqual(self.bridge.get_report(0x56), bytes.fromhex('5601014000'))
        self.create()
        self.assertEqual(self.bridge.get_report(0x56), bytes.fromhex('5602010000'))
        self.create()
        self.assertEqual(self.bridge.get_report(0x56)[2], 2)

    def test_custom_and_reserved_types_fail_without_consuming_memory(self):
        for kind in (3, 13, 18):
            self.assertEqual(self.create(kind), 0)
            self.assertEqual(self.bridge.get_report(0x56)[2], 3)
            self.assertEqual(len(self.bridge.effects), 0)

    def test_repeated_allocate_free_does_not_exhaust_or_leak(self):
        for _ in range(2000):
            block = self.create()
            self.assertEqual(block, 1)
            self.bridge.seen(bytes((0x5b, block)))
        self.assertEqual(self.bridge.pool.available, 2560)

    def test_unknown_blocks_never_create_effects(self):
        self.bridge.seen(bytes((0x55, 40, 1, 0)))
        self.bridge.seen(bytes((0x5a, 0, 1, 1)))
        self.bridge.seen(bytes((0x5b, 255)))
        self.assertEqual(self.bridge.effects, {})

    def test_capacity_cannot_exceed_descriptor_or_be_empty(self):
        for capacity in (0, 41, -1, 1.5, True):
            with self.assertRaises(ValueError):
                M.PIDSteeringBridge(capacity=capacity)


class TimelineTests(LifecycleVectors):
    def control(self, code):
        self.bridge.seen(bytes((0x50, code)))

    def test_finite_repetitions_end_at_exact_boundary(self):
        block = self.constant(duration=50, loops=3)
        for now in (0, .049, .05, .075, .149):
            self.now = now
            self.assertEqual(self.bridge.raw_level(), 16000)
        self.assertEqual(self.bridge.effects[block]['loop_index'], 2)
        self.now = .15
        self.assertEqual(self.bridge.raw_level(), 0)

    def test_255_loops_is_infinite_and_zero_is_no_execution(self):
        block = self.constant(duration=10, loops=255)
        self.now = 10000
        self.assertEqual(self.bridge.raw_level(), 16000)
        self.bridge.seen(bytes((0x5a, block, 1, 0)))
        self.assertEqual(self.bridge.raw_level(), 0)
        self.assertFalse(self.bridge.effects[block]['running'])

    def test_254_repetitions_are_finite(self):
        self.constant(duration=10, loops=254)
        self.now = 2.539
        self.assertEqual(self.bridge.raw_level(), 16000)
        self.now = 2.54
        self.assertEqual(self.bridge.raw_level(), 0)

    def test_delay_occurs_once_not_before_each_repetition(self):
        self.constant(duration=50, delay=20, loops=3)
        self.now = .01
        self.assertEqual(self.bridge.raw_level(), 0)
        self.now = .075
        self.assertEqual(self.bridge.raw_level(), 16000)
        self.now = .17
        self.assertEqual(self.bridge.raw_level(), 0)

    def test_envelope_restarts_on_each_loop(self):
        block = self.constant(duration=100, loops=3)
        self.bridge.seen(bytes((0x52, block)) + struct.pack('<HHHH', 0, 0, 100, 0))
        for now, expected in ((.05, 8000), (.1, 0), (.15, 8000), (.3, 0)):
            self.now = now
            self.assertEqual(self.bridge.raw_level(), expected)

    def test_zero_duration_with_infinite_count_does_not_spin(self):
        block = self.constant(duration=0, loops=255)
        self.assertEqual(self.bridge.raw_level(), 0)
        self.assertFalse(self.bridge.effects[block]['running'])

    def test_pause_freezes_delay_and_continue_preserves_remaining_time(self):
        self.constant(duration=100, delay=100)
        self.now = .05
        self.control(5)
        self.now = 10
        self.assertEqual(self.bridge.raw_level(), 0)
        self.control(6)
        self.now = 10.049
        self.assertEqual(self.bridge.raw_level(), 0)
        self.now = 10.05
        self.assertEqual(self.bridge.raw_level(), 16000)
        self.now = 10.15
        self.assertEqual(self.bridge.raw_level(), 0)

    def test_pause_freezes_envelope_phase_and_repetitions(self):
        block = self.constant(duration=100, loops=2)
        self.bridge.seen(bytes((0x52, block)) + struct.pack('<HHHH', 0, 0, 100, 0))
        self.now = .15
        self.assertEqual(self.bridge.raw_level(), 8000)
        self.control(5)
        self.now = 4
        self.control(6)
        self.assertEqual(self.bridge.raw_level(), 8000)
        self.now = 4.05
        self.assertEqual(self.bridge.raw_level(), 0)

    def test_pause_continue_are_idempotent(self):
        self.constant(duration=100)
        self.now = .02
        self.control(5)
        self.now = .03
        self.control(5)
        self.now = 1
        self.control(6)
        self.control(6)
        self.now = 1.08
        self.assertEqual(self.bridge.raw_level(), 0)

    def test_start_while_paused_begins_from_resume(self):
        block = self.constant(duration=100)
        self.now = .02
        self.control(5)
        self.now = 1
        self.bridge.seen(bytes((0x5a, block, 1, 1)))
        self.now = 2
        self.control(6)
        self.now = 2.099
        self.assertEqual(self.bridge.raw_level(), 16000)
        self.now = 2.1
        self.assertEqual(self.bridge.raw_level(), 0)

    def test_disable_mutes_actuator_but_time_keeps_running(self):
        block = self.constant(duration=100)
        self.now = .02
        self.control(2)
        self.assertTrue(self.bridge.effects[block]['running'])
        self.assertFalse(self.bridge.paused)
        self.assertEqual(self.bridge.raw_level(), 0)
        self.now = .05
        self.control(1)
        self.assertEqual(self.bridge.raw_level(), 16000)
        self.now = .06
        self.control(2)
        self.now = 1
        self.control(1)
        self.assertEqual(self.bridge.raw_level(), 0)

    def test_start_solo_stops_others_and_restarts_target(self):
        first = self.constant(10000)
        second = self.constant(8000)
        self.assertEqual(self.bridge.raw_level(), 18000)
        self.now = .05
        self.bridge.seen(bytes((0x5a, second, 2, 1)))
        self.assertFalse(self.bridge.effects[first]['running'])
        self.assertEqual(self.bridge.effects[second]['started_at'], .05)
        self.assertEqual(self.bridge.raw_level(), 8000)

    def test_stop_all_preserves_allocations_and_free_releases_only_target(self):
        first = self.constant(10000)
        second = self.constant(8000)
        self.control(3)
        self.assertEqual(len(self.bridge.effects), 2)
        self.assertEqual(self.bridge.raw_level(), 0)
        self.bridge.seen(bytes((0x5a, second, 1, 1)))
        self.bridge.seen(bytes((0x5b, first)))
        self.assertEqual(self.bridge.raw_level(), 8000)

    def test_update_after_expiry_does_not_resurrect_effect(self):
        block = self.constant(duration=50)
        self.now = .1
        self.configure(block, duration=1000)
        self.assertEqual(self.bridge.raw_level(), 0)
        self.bridge.seen(bytes((0x5a, block, 1, 1)))
        self.assertEqual(self.bridge.raw_level(), 16000)

    def test_reset_clears_pause_disable_gain_and_old_effects(self):
        self.constant()
        self.control(5)
        self.control(2)
        self.bridge.seen(bytes((0x59, 0)))
        self.control(4)
        self.assertEqual(self.bridge.effects, {})
        self.assertFalse(self.bridge.paused)
        self.assertTrue(self.bridge.actuators_enabled)
        self.assertEqual(self.bridge.gain, 1)
