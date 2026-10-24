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
