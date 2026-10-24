# SPDX-License-Identifier: GPL-2.0-only
"""Replay sem I/O de dispositivos; valida respostas, estado e saída simulada."""
import argparse
import json
import sys
import gzip

from .capture import SCHEMA, VERSION, records
from .diagnostics import classify_uhid_result
from .passive import PassiveSession


def replay(path, *, verify_compatible=False):
    session = None
    try:
        opener = gzip.open if str(path).endswith('.gz') else open
        with opener(path, 'rt', encoding='utf-8') as stream:
            iterator = iter(records(stream))
            header = next(iterator, None)
            if (not header or header.get('kind') != 'header' or
                    header.get('schema') != SCHEMA or header.get('version') != VERSION):
                raise ValueError('Formato de captura incompatível')
            metadata = header['metadata']
            session = PassiveSession(metadata['origin_ns'], **metadata['config'])
            current_metadata = session.metadata()
            implementation_match = metadata.get('implementation_sha256') == current_metadata['implementation_sha256']
            for key, value in current_metadata.items():
                if metadata.get(key) != value:
                    if key == 'implementation_sha256' and verify_compatible:
                        continue  # Só o hash; todas as respostas/forças ainda precisam coincidir.
                    raise ValueError('Contrato de replay incompatível: ' + key)
            steps = force_samples = game_steps = 0
            footer = None
            delivery_errors = 0
            for record in iterator:
                if footer is not None:
                    raise ValueError('Registros depois do encerramento')
                if record.get('kind') == 'footer':
                    footer = record
                    if not session.core.closed or record['summary'] != session.summary():
                        raise ValueError('Resumo ou estado final divergente')
                elif record.get('kind') == 'step':
                    result = session.apply(record['action'], record['data'], record['timestamp_ns'])
                    if result != record['result']:
                        divergent = sorted(k for k in set(result) | set(record['result'])
                                           if result.get(k) != record['result'].get(k))
                        raise ValueError(f'Replay divergente no registro {record["seq"]}: {divergent}')
                    if 'classification' in record:
                        classification = classify_uhid_result(result) if record['action'] == 'uhid' else None
                        if record['classification'] != classification:
                            raise ValueError(f'Classificação divergente no registro {record["seq"]}')
                    delivery = record['delivery']
                    timing = record['timing']
                    if any(type(timing.get(key)) is not int or timing[key] < 0
                           for key in ('processing_ns', 'delivery_ns')):
                        raise ValueError('Medição de latência inválida')
                    if delivery['mode'] not in ('virtual', 'simulated') or not isinstance(delivery['sent'], int):
                        raise ValueError('Entrega de respostas inválida')
                    if not 0 <= delivery['sent'] <= len(result['writes_hex']):
                        raise ValueError('Quantidade de respostas inválida')
                    if delivery['mode'] == 'virtual' and delivery['sent'] != len(result['writes_hex']) and not delivery['error']:
                        raise ValueError('Respostas omitidas sem erro de entrega')
                    delivery_errors += int(delivery['error'] is not None)
                    steps += 1
                    force_samples += int(result['final_force'] != 0)
                    game_steps += int(result['phase'] == 'game_operator_marked')
                else:
                    raise ValueError('Registro de captura desconhecido')
            complete = bool(footer and footer.get('complete') and not delivery_errors)
            return dict(verified=True, complete=complete, steps=steps,
                        implementation_match=implementation_match,
                        compatibility_verified=bool(not implementation_match and verify_compatible and complete),
                        delivery_errors=delivery_errors,
                        nonzero_force_samples=force_samples, game_marked_steps=game_steps,
                        race_physically_validated=False,
                        warning=None if complete else 'Captura interrompida; somente prefixo validado',
                        summary=session.summary())
    finally:
        if session is not None:
            session.core.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture')
    parser.add_argument('--verify-compatible', action='store_true',
                        help='Aceita fontes diferentes somente se todos os resultados semânticos coincidirem')
    parser.add_argument('--physical-trace', help='Confere também o trace supervisionado em mock, correlacionado ao núcleo')
    args = parser.parse_args(argv)
    try:
        result = replay(args.capture, verify_compatible=args.verify_compatible)
        if args.physical_trace:
            from .physical_replay import replay_physical
            result['physical'] = replay_physical(args.physical_trace, core_capture=args.capture,
                                                verify_compatible=args.verify_compatible, _core_complete=result['complete'])
            result['complete'] = result['complete'] and result['physical']['complete']
    except (ValueError, KeyError, TypeError, OSError, EOFError) as exc:
        print('Replay inválido: ' + str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result['complete'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
