# SPDX-License-Identifier: GPL-2.0-only
"""JSONL v1: registros encadeados, streaming e sem buffers ilimitados."""
import hashlib
import json
import gzip
import time
from pathlib import Path

SCHEMA = 'dfgt-bridge-passive'
VERSION = 1
MAX_LINE = 2 * 1024 * 1024


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                      allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def implementation_hash():
    """Identifica todos os módulos do pacote; não depende de Git instalado."""
    root = Path(__file__).parent
    checksum = hashlib.sha256()
    for path in sorted(root.rglob('*.py')):
        checksum.update(str(path.relative_to(root)).encode())
        checksum.update(path.read_bytes())
    return checksum.hexdigest()


class CaptureWriter:
    def __init__(self, path, metadata):
        self.file = (gzip.open(path, 'xt', encoding='utf-8', compresslevel=1)
                     if str(path).endswith('.gz') else
                     open(path, 'x', encoding='utf-8', buffering=131072))
        self.sequence = 0
        self.previous = '0' * 64
        try:
            self.append({'kind': 'header', 'schema': SCHEMA, 'version': VERSION,
                         'metadata': metadata})
            self.file.flush()
        except BaseException:
            self.file.close()
            raise

    def append(self, record):
        value = dict(record, seq=self.sequence, previous=self.previous)
        value['sha256'] = digest(value)
        line = canonical(value) + '\n'
        if len(line) > MAX_LINE:
            raise ValueError('Registro excede limite de captura')
        self.file.write(line)
        self.sequence += 1
        self.previous = value['sha256']

    def flush(self):
        self.file.flush()

    def close(self):
        self.file.close()


def records(stream):
    """Valida framing, sequência e integridade antes de fornecer cada registro."""
    previous, sequence = '0' * 64, 0
    while True:
        line = stream.readline(MAX_LINE + 1)
        if not line:
            return
        if len(line) > MAX_LINE or not line.endswith('\n'):
            raise ValueError('Linha truncada ou excessiva na captura')
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError('Registro deve ser objeto')
        checksum = value.pop('sha256', None)
        if value.get('seq') != sequence or value.get('previous') != previous or digest(value) != checksum:
            raise ValueError('Captura perdeu, duplicou, reordenou ou alterou registros')
        previous, sequence = checksum, sequence + 1
        value['sha256'] = checksum
        yield value


class Recorder:
    """ACK antes de serialização/disco. Sem thread ou fila de comandos."""
    def __init__(self, session, writer=None, send=None):
        self.session, self.writer, self.send = session, writer, send

    def dispatch(self, action, data, timestamp_ns):
        started = time.perf_counter_ns()
        result = self.session.apply(action, data, timestamp_ns)
        processed = time.perf_counter_ns()
        sent = 0
        error = None
        try:
            if self.send is not None:
                for event in result['writes_hex']:
                    self.send(bytes.fromhex(event))
                    sent += 1
            return result
        except BaseException as exc:
            error = type(exc).__name__ + ': ' + str(exc)
            raise
        finally:
            delivered = time.perf_counter_ns()
            if self.writer is not None:
                self.writer.append(dict(kind='step', action=action, data=data,
                                        timestamp_ns=timestamp_ns, result=result,
                                        delivery=dict(mode='virtual' if self.send else 'simulated',
                                                      sent=sent, error=error),
                                        timing=dict(processing_ns=processed-started,
                                                    delivery_ns=delivered-processed)))
