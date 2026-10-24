# SPDX-License-Identifier: GPL-2.0-only
"""Replay sem I/O de dispositivos; valida respostas, estado e saída simulada."""
import argparse
import json
import sys
import gzip

from .capture import SCHEMA, VERSION, records
from .passive import PassiveSession


def replay(path):
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
            for key, value in session.metadata().items():
                if metadata.get(key) != value:
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
                    delivery = record['delivery']
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
    args = parser.parse_args(argv)
    try:
        result = replay(args.capture)
    except (ValueError, KeyError, TypeError, OSError, EOFError) as exc:
        print('Replay inválido: ' + str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result['complete'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
