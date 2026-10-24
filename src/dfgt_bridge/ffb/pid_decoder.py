# SPDX-License-Identifier: GPL-2.0-only
"""Contrato dos relatórios do descritor preservado; sem framing UHID ou I/O."""
import errno
import struct


OUTPUT_LENGTHS = {0x50: 2, 0x51: 18, 0x52: 10, 0x53: 15, 0x55: 4,
                  0x58: 4, 0x59: 2, 0x5a: 4, 0x5b: 2, 0x5d: 10}
FEATURE_LENGTHS = {0x54: 4}


class ReportError(ValueError):
    def __init__(self, reason, code=errno.EINVAL):
        super().__init__(reason)
        self.reason, self.code = reason, code


def validate_report(packet, *, feature=False, number=None):
    if not isinstance(packet, (bytes, bytearray, memoryview)) or not packet:
        raise ReportError('short_packet')
    packet = bytes(packet)
    op = packet[0]
    if number is not None and number != op:
        raise ReportError('report_id_mismatch')
    lengths = FEATURE_LENGTHS if feature else OUTPUT_LENGTHS
    if op not in lengths:
        raise ReportError('unsupported_report', errno.EOPNOTSUPP)
    if len(packet) != lengths[op]:
        raise ReportError('invalid_report_length')
    if feature:
        if not 1 <= packet[1] <= 18 or packet[3] & 0xfe:
            raise ReportError('invalid_create')
    elif op == 0x50 and not 1 <= packet[1] <= 6:
        raise ReportError('invalid_device_control')
    elif op == 0x51:
        duration, repeat, sample, delay = struct.unpack_from('<HHHH', packet, 3)
        if any(value > 35900 for value in struct.unpack_from('<HH', packet, 14)):
            raise ReportError('invalid_direction')
        if not 1 <= packet[2] <= 12 or packet[11] > 100 or packet[13] & 0xf8:
            raise ReportError('invalid_set_effect')
        if (duration > 32767 and duration != 65535) or (repeat > 32767 and repeat != 65535) or max(sample, delay) > 32767:
            raise ReportError('invalid_effect_time')
    elif op == 0x52 and any(value > 32767 for value in struct.unpack_from('<HHHH', packet, 2)):
        raise ReportError('invalid_envelope')
    elif op == 0x53 and packet[2] not in (0, 1):
        raise ReportError('invalid_condition_axis')
    elif op == 0x5a and packet[2] not in (1, 2, 3):
        raise ReportError('invalid_effect_operation')
    elif op == 0x5d:
        magnitude, offset, phase, period = struct.unpack_from('<HhHH', packet, 2)
        if magnitude > 32767 or phase > 35900 or period > 32767:
            raise ReportError('invalid_periodic')
    return packet
