# SPDX-License-Identifier: GPL-2.0-only
"""Classificação observacional. Não aceita, executa ou retransmite comandos.

Vetores exatos encontrados nas duas capturas reais; referências e índices em
docs/CAPTURE_COMPARISON_20261024.md. O primeiro byte desses pacotes é opcode
Logitech, não um Report ID anunciado pelo nosso descritor PID.
"""
from .ffb.pid_decoder import OUTPUT_LENGTHS, FEATURE_LENGTHS

KNOWN_LOGITECH = {
    bytes.fromhex('f5000000000000'): 'autocenter_off',
    bytes.fromhex('0d000000000000'): 'fixed_loop_off',
    bytes.fromhex('f8120000000000'): 'leds_off',
}
PID_REPORTS = frozenset(OUTPUT_LENGTHS) | frozenset(FEATURE_LENGTHS) | {0x56, 0x57, 1}


def classify_uhid_result(result):
    """Mantém erro bruto; reconhece somente os três pacotes integrais acima."""
    info, error = result['info'], result['error']
    packet = bytes.fromhex(info.get('packet_hex') or '')
    command = None
    if (error == 'unsupported_report' and info.get('report_type') == 1
            and info.get('event_type') in (6, 13) and packet in KNOWN_LOGITECH
            and info.get('declared_size') == len(packet)):
        category, command = 'proprietary_ignored', KNOWN_LOGITECH[packet]
    elif error is None:
        category = 'pid' if info.get('event_type') in (6, 9, 13) else 'uhid_lifecycle'
    elif (error.startswith(('short_', 'invalid_', 'oversized_')) or error == 'report_id_mismatch'
          or (error == 'unsupported_report' and packet and packet[0] in (0xf5, 0x0d, 0xf8)
              and len(packet) != 7)):
        category = 'malformed_report'
    elif error in ('pid_missing_block', 'effect_family_mismatch', 'effect_type_mismatch'):
        category = 'effect_state_error'
    elif error == 'unsupported_report':
        category = 'unknown_report'
    elif info.get('report_id') in PID_REPORTS and error.startswith('report_type_'):
        category = 'pid_rejected'
    else:
        category = 'transport_error'
    return dict(category=category, command=command, raw_error=error)
