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
            core_header = next(source_records, None)
            source_effects, source_gain = {}, 1.0
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
            if source is not None and 'physical_config' in core_header['metadata']:
                if core_header['metadata']['physical_config'] != metadata['config']:
                    raise ValueError('Perfil físico difere da captura principal')
            match = metadata['implementation_sha256'] == implementation_hash()
            if metadata.get('diagnostics_version', 1) not in (1, 2):
                raise ValueError('Versão de diagnóstico físico desconhecida')
            if not match and not verify_compatible:
                raise ValueError('Fontes do trace físico diferentes; use --verify-compatible para conferir resultados')
            backend = MockBackend(clock=lambda: policy.now_ns/1e9)
            policy = SupervisedPolicy(backend, **metadata['config'])
            def verify_result(expected):
                actual = policy.snapshot()
                # Extensão aditiva v2: traces v1 não continham esse diagnóstico.
                # Todos os campos e decisões originais continuam sendo comparados.
                if metadata.get('diagnostics_version', 1) == 1 and 'force_pipeline' not in expected:
                    actual.pop('force_pipeline')
                return actual == expected
            steps = frames = 0
            footer = None
            for record in iterator:
                if footer is not None:
                    raise ValueError('Dados depois do footer físico')
                if record['kind'] == 'footer':
                    footer = record
                    if not verify_result(record['result']):
                        raise ValueError('Resumo físico divergente')
                    continue
                if record['kind'] != 'physical_step':
                    raise ValueError('Registro físico desconhecido')
                policy.apply(record['action'], record['data'], record['timestamp_ns'])
                for key, value in record.get('io', {}).items():
                    lower, upper = ((-32768, 32767) if key in ('level_uploaded', 'level_commanded') else
                                    (0, 65535) if key == 'gain_commanded' else (0, None))
                    if type(value) is not int or value < lower or (upper is not None and value > upper):
                        raise ValueError('Telemetria EVDEV inválida')
                if ('level_commanded' in record.get('io', {}) and
                        record['io']['level_commanded'] != record['result']['evdev_level']):
                    raise ValueError('Magnitude EVDEV diverge da política física')
                if not verify_result(record['result']):
                    raise ValueError(f'Política física divergiu no registro {record["seq"]}')
                steps += 1
                if record['action'] == 'frame':
                    frames += 1
                    if source is not None:
                        frame = record['data']
                        for original in source_records:
                            if original.get('kind') != 'step':
                                continue
                            transitions = original['result']['transitions']
                            if 'effects' in transitions:
                                source_effects = transitions['effects']
                            source_gain = transitions.get('gain', source_gain)
                            if (original['timestamp_ns'] == frame['calculated_ns'] and
                                    original['result']['components'] == frame['components']):
                                effects = [(int(i), v) for i, v in sorted(source_effects.items(), key=lambda pair: int(pair[0]))]
                                expected_game = dict(pid_gain=source_gain,
                                    constant_levels=[[i, v['level']] for i, v in effects if v['kind'] == 1 and v['running']])
                                expected_effects = [[i, v['kind'], v['running'], v['gain']] for i, v in effects]
                                if (('game_force' in frame and frame['game_force'] != expected_game) or
                                        ('effects' in frame and frame['effects'] != expected_effects)):
                                    raise ValueError('Parâmetros PID do trace divergem da captura principal')
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
