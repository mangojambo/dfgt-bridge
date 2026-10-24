# SPDX-License-Identifier: GPL-2.0-only
"""Replay da política EVDEV em mock; nunca importa o backend de hardware."""
import gzip

from .backends.mock import MockBackend
from .capture import SCHEMA, VERSION, records, implementation_hash
from .supervised import SupervisedPolicy


def replay_physical(path, *, core_capture=None, verify_compatible=False, _core_complete=None):
    policy = source = None
    try:
        if core_capture:
            if _core_complete is None:
                from .replay import replay
                _core_complete = replay(core_capture, verify_compatible=verify_compatible)['complete']
            opener = gzip.open if str(core_capture).endswith('.gz') else open
            source = opener(core_capture, 'rt', encoding='utf-8')
            source_records = iter(records(source))
            next(source_records, None)
        opener = gzip.open if str(path).endswith('.gz') else open
        with opener(path, 'rt', encoding='utf-8') as stream:
            iterator = iter(records(stream))
            header = next(iterator, None)
            if not header:
                raise ValueError('Trace físico vazio')
            metadata = header['metadata']
            if (header['schema'] != SCHEMA or header['version'] != VERSION or
                    metadata.get('kind') != 'physical_policy'):
                raise ValueError('Trace físico incompatível')
            match = metadata['implementation_sha256'] == implementation_hash()
            if not match and not verify_compatible:
                raise ValueError('Fontes do trace físico diferentes; use --verify-compatible para conferir resultados')
            backend = MockBackend(clock=lambda: policy.now_ns/1e9)
            policy = SupervisedPolicy(backend, **metadata['config'])
            steps = frames = 0
            footer = None
            for record in iterator:
                if footer is not None:
                    raise ValueError('Dados depois do footer físico')
                if record['kind'] == 'footer':
                    footer = record
                    if record['result'] != policy.snapshot():
                        raise ValueError('Resumo físico divergente')
                    continue
                if record['kind'] != 'physical_step':
                    raise ValueError('Registro físico desconhecido')
                result = policy.apply(record['action'], record['data'], record['timestamp_ns'])
                if 'io' in record and any(type(v) is not int or v < 0 for v in record['io'].values()):
                    raise ValueError('Telemetria de latência EVDEV inválida')
                if result != record['result']:
                    raise ValueError(f'Política física divergiu no registro {record["seq"]}')
                steps += 1
                if record['action'] == 'frame':
                    frames += 1
                    if source is not None:
                        frame = record['data']
                        for original in source_records:
                            if original.get('kind') != 'step':
                                continue
                            if (original['timestamp_ns'] == frame['calculated_ns'] and
                                    original['result']['components'] == frame['components']):
                                break
                        else:
                            raise ValueError('Frame físico não corresponde ao renderer da captura principal')
            return dict(verified=True, complete=bool(footer and footer['complete'] and policy.closed
                                                    and (source is None or _core_complete)),
                        steps=steps, frames=frames, core_correlated=source is not None,
                        implementation_match=match, summary=policy.snapshot(), physical_hardware_validated=False)
    finally:
        if source is not None:
            source.close()
        if policy is not None and not policy.closed:
            policy.apply('close', {}, policy.now_ns)
