"""Integração de bytes UHID/PID em memória, sem abrir qualquer dispositivo."""
import copy
import csv
import errno
import struct
import unittest

from test_ffb_audit import FIXTURES, M
from dfgt_bridge.ffb.pid_decoder import OUTPUT_LENGTHS, ReportError
from dfgt_bridge.ffb.transport import PIDTransport


def output(packet, rtype=1):
    return struct.pack('=I', 6) + packet.ljust(4096, b'\0') + struct.pack('=HB', len(packet), rtype)


def set_report(packet, request_id=1, rtype=1, number=None):
    return struct.pack('=IIBBH', 13, request_id, packet[0] if number is None else number,
                       rtype, len(packet)) + packet


def get_report(number, request_id=1, rtype=0):
    return struct.pack('=IIBB', 9, request_id, number, rtype)


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.now = 0.0
        self.engine = M.PIDSteeringBridge(clock=lambda: self.now)
        self.engine.arm()
        self.transport = PIDTransport(self.engine)

    def create(self, request_id=1):
        replies = self.transport.handle(set_report(bytes.fromhex('54010000'), request_id, 0))
        self.assertEqual(replies, (struct.pack('=IIH', 14, request_id, 0),))
        return self.engine.last_block

    def test_feature_allocation_and_exact_native_uhid_replies(self):
        self.assertEqual(self.transport.handle(get_report(0x57, 40)),
                         (struct.pack('=IIHH', 10, 40, 0, 5) + bytes.fromhex('57000a2801'),))
        self.create(41)
        self.assertEqual(self.transport.handle(get_report(0x56, 42)),
                         (struct.pack('=IIHH', 10, 42, 0, 5) + bytes.fromhex('560101c009'),))

    def test_output_and_set_report_share_one_decoder(self):
        block = self.create()
        self.transport.handle(output(bytes((0x55, block, 0x80, 0x3e))))
        self.transport.handle(set_report(bytes((0x5a, block, 1, 1)), 2))
        self.assertEqual(self.engine.raw_level(), 16000)
        self.transport.handle(set_report(bytes((0x55, block, 0x40, 0x1f)), 3))
        self.assertEqual(self.engine.raw_level(), 8000)
        self.transport.handle(output(bytes((0x5a, block, 3, 0))))
        self.assertEqual(self.engine.raw_level(), 0)

    def test_feature_via_uhid_output_is_also_typed_correctly(self):
        self.assertEqual(self.transport.handle(output(bytes.fromhex('54010000'), 0)), ())
        self.assertEqual(list(self.engine.effects), [1])

    def test_uhid_types_are_not_usb_types(self):
        for wrong in (1, 2, 3):  # Feature no UHID é 0, não USB 3 ou kernel 2.
            result = self.transport.handle(set_report(bytes.fromhex('54010000'), wrong, wrong))
            self.assertNotEqual(struct.unpack_from('=H', result[0], 8)[0], 0)
        self.assertEqual(self.engine.effects, {})

    def test_input_set_and_wrong_feature_output_are_rejected(self):
        for rtype in (0, 2, 3):
            reply = self.transport.handle(set_report(bytes.fromhex('5900'), 1+rtype, rtype))[0]
            self.assertNotEqual(struct.unpack_from('=H', reply, 8)[0], 0)
        self.assertEqual(self.engine.gain, 1)

    def test_input_and_output_report_50_are_distinct(self):
        self.assertEqual(self.transport.handle(get_report(0x50, 1, 2))[0][12:], bytes.fromhex('500201'))
        reply = self.transport.handle(get_report(0x50, 2, 0))[0]
        self.assertEqual(struct.unpack_from('=H', reply, 8)[0], errno.EOPNOTSUPP)
        self.assertFalse(self.engine.paused)

    def test_gets_are_read_only_and_do_not_allocate_or_apply_output(self):
        for number, rtype in ((0x54, 0), (0x59, 1), (0x01, 2), (0x57, 3)):
            reply = self.transport.handle(get_report(number, number, rtype))[0]
            self.assertNotEqual(struct.unpack_from('=H', reply, 8)[0], 0)
            self.assertEqual(struct.unpack_from('=H', reply, 10)[0], 0)
        self.assertEqual(self.engine.effects, {})

    def test_id_mismatch_and_incorrect_lengths_do_not_mutate_pool(self):
        for packet, number in ((bytes.fromhex('54010000'), 0x55),
                               (bytes.fromhex('5401'), 0x54),
                               (bytes.fromhex('5401000000'), 0x54)):
            reply = self.transport.handle(set_report(packet, number, 0, number))[0]
            self.assertEqual(struct.unpack_from('=H', reply, 8)[0], errno.EINVAL)
        self.assertEqual(self.engine.effects, {})

    def test_reserved_create_bits_and_invalid_kind_are_rejected(self):
        for index, packet in enumerate((bytes.fromhex('54010002'), bytes.fromhex('54000000'))):
            reply = self.transport.handle(set_report(packet, index, 0))[0]
            self.assertEqual(struct.unpack_from('=H', reply, 8)[0], errno.EINVAL)
        self.assertEqual(self.engine.effects, {})

    def test_full_and_unsupported_allocations_use_block_load_status_not_set_errno(self):
        self.engine = M.PIDSteeringBridge(capacity=1)
        self.transport = PIDTransport(self.engine)
        self.create(1)
        self.create(2)
        self.assertEqual(self.engine.get_report(0x56)[2], 2)
        self.transport.handle(set_report(bytes.fromhex('54030000'), 3, 0))
        self.assertEqual(self.engine.get_report(0x56)[2], 3)

    def test_duplicate_control_request_does_not_allocate_twice(self):
        event = set_report(bytes.fromhex('54010000'), 100, 0)
        first = self.transport.handle(event)
        self.assertEqual(self.transport.handle(event), first)
        self.assertEqual(len(self.engine.effects), 1)
        self.assertEqual(self.transport.counts['duplicates'], 1)

    def test_reused_request_id_with_changed_payload_is_rejected(self):
        self.create(1)
        reply = self.transport.handle(set_report(bytes.fromhex('54020000'), 1, 0))[0]
        self.assertEqual(struct.unpack_from('=H', reply, 8)[0], errno.EINVAL)
        self.assertEqual(len(self.engine.effects), 1)

    def test_duplicate_start_request_does_not_restart_timeline(self):
        block = self.create()
        event = set_report(bytes((0x5a, block, 1, 1)), 2)
        self.transport.handle(event)
        self.now = .1
        self.transport.handle(event)
        self.assertEqual(self.engine.effects[block]['started_at'], 0)

    def test_two_distinct_identical_starts_are_not_mistaken_for_duplicates(self):
        block = self.create()
        packet = bytes((0x5a, block, 1, 1))
        self.transport.handle(output(packet))
        self.now = .1
        self.transport.handle(set_report(packet, 2))
        self.assertEqual(self.engine.effects[block]['started_at'], .1)

    def test_cross_path_duplicate_is_applied_once_when_correlation_is_known(self):
        block = self.create()
        packet = bytes((0x5a, block, 1, 1))
        self.transport.handle(output(packet), observation_id='op-123')
        self.now = .1
        self.transport.handle(set_report(packet, 2), observation_id='op-123')
        self.assertEqual(self.engine.effects[block]['started_at'], 0)
        self.assertEqual(self.engine.pid_counts['0x5a'], 1)

    def test_request_and_observation_caches_are_bounded(self):
        transport = PIDTransport(self.engine, cache_size=8)
        for request in range(1000):
            transport.handle(set_report(bytes.fromhex('59ff'), request), observation_id=request)
        self.assertEqual(len(transport.requests), 8)
        self.assertEqual(len(transport.observations), 8)
        self.assertEqual(transport.counts['writes'], 1000)

    def test_short_payload_is_zero_extended_per_uhid_uapi(self):
        block = self.create()
        event = struct.pack('=IIBBH', 13, 2, 0x55, 1, 4) + bytes((0x55, block, 64))
        self.assertEqual(self.transport.handle(event)[0], struct.pack('=IIH', 14, 2, 0))
        self.assertEqual(self.engine.effects[block]['level'], 64)

    def test_truncated_headers_are_rejected_with_reply_if_id_is_available(self):
        self.assertEqual(self.transport.handle(b'\x0d'), ())
        self.assertEqual(self.transport.handle(struct.pack('=II', 13, 8)),
                         (struct.pack('=IIH', 14, 8, errno.EINVAL),))
        self.assertEqual(self.transport.handle(struct.pack('=II', 9, 9)),
                         (struct.pack('=IIHH', 10, 9, errno.EINVAL, 0),))

    def test_oversized_declarations_and_frames_are_rejected(self):
        event = struct.pack('=IIBBH', 13, 1, 0x55, 1, 4097)
        self.assertEqual(struct.unpack_from('=H', self.transport.handle(event)[0], 8)[0], errno.EMSGSIZE)
        event = get_report(0x57, 2).ljust(4381, b'\0')
        self.assertEqual(struct.unpack_from('=H', self.transport.handle(event)[0], 8)[0], errno.EMSGSIZE)
        self.assertEqual(self.transport.handle(struct.pack('=I', 6)), ())

    def test_full_padded_uhid_events_are_accepted(self):
        event = set_report(bytes.fromhex('54010000'), 4, 0).ljust(4380, b'\0')
        self.assertEqual(self.transport.handle(event)[0], struct.pack('=IIH', 14, 4, 0))

    def test_malformed_output_does_not_change_previously_valid_effect(self):
        block = self.create()
        self.engine.write_output(bytes((0x55, block, 0x80, 0x3e)))
        before = copy.deepcopy(self.engine.effects)
        for op, length in OUTPUT_LENGTHS.items():
            for invalid_length in (length-1, length+1):
                packet = bytes((op,)) + bytes(invalid_length-1)
                self.transport.handle(output(packet))
                self.assertEqual(self.engine.effects, before)

    def test_invalid_blocks_and_family_mismatches_do_not_create_state(self):
        block = self.create()
        for packet in (bytes((0x55, 0, 1, 0)), bytes((0x55, 41, 1, 0)),
                       bytes((0x58, block, 1, 1)), bytes((0x5b, 255))):
            with self.assertRaises(ReportError):
                self.engine.write_output(packet)
        self.assertEqual(len(self.engine.effects), 1)

    def test_close_and_stop_clear_state_without_automatic_rearming(self):
        self.create()
        self.transport.handle(struct.pack('=I', 3))
        self.assertEqual(self.engine.effects, {})
        self.assertFalse(self.engine.armed)
        self.transport.handle(struct.pack('=I', 4))
        reply = self.transport.handle(set_report(bytes.fromhex('54010000'), 2, 0))[0]
        self.assertEqual(struct.unpack_from('=H', reply, 8)[0], errno.EPIPE)

    def test_real_csv_replay_uses_captured_uhid_types_without_motor(self):
        total = 0
        for path in sorted(FIXTURES.glob('*.csv')):
            self.setUp()
            with path.open() as handle:
                for row in csv.DictReader(handle):
                    total += 1
                    self.now = int(row['timestamp_ns']) / 1e9
                    packet, rtype = bytes.fromhex(row['hex_bytes']), int(row['report_type'])
                    if row['kind'] == 'OUTPUT':
                        event = output(packet, rtype)
                    elif row['kind'] == 'SET_REPORT':
                        event = set_report(packet, total, rtype, int(row['report_num']))
                    else:
                        event = get_report(int(row['report_num']), total, rtype)
                    self.transport.handle(event)
                    self.assertEqual(self.engine.raw_level(), 0)
        self.assertEqual(total, 132)
