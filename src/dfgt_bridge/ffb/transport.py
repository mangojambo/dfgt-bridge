# SPDX-License-Identifier: GPL-2.0-only
"""Framing UHID em bytes. Não abre descritores nem chama o backend de saída."""
from collections import Counter, OrderedDict
import errno
import struct

from .pid_decoder import ReportError


UHID_STOP, UHID_CLOSE, UHID_OUTPUT = 3, 5, 6
UHID_GET_REPORT, UHID_GET_REPORT_REPLY = 9, 10
UHID_SET_REPORT, UHID_SET_REPORT_REPLY = 13, 14
UHID_FEATURE_REPORT, UHID_OUTPUT_REPORT, UHID_INPUT_REPORT = 0, 1, 2
UHID_DATA_MAX, UHID_EVENT_SIZE = 4096, 4380


class PIDTransport:
    """Uma chamada por evento; caches limitados, sem filas de força.

    IDs de requisição deduplicam retries de controle. observation_id só deve
    ser usado quando o chamador SABE que dois registros são a mesma operação.
    UHID_OUTPUT não tem um ID que permita inferir isso por comparação de bytes.
    """
    def __init__(self, engine, cache_size=128, *, close_terminates=True):
        if type(cache_size) is not int or not 1 <= cache_size <= 4096:
            raise ValueError('Cache deve estar em 1..4096')
        self.engine, self.cache_size = engine, cache_size
        self.requests, self.observations = OrderedDict(), OrderedDict()
        self.counts = Counter()
        self.last_error = None
        self.closed = False
        self.close_terminates = close_terminates

    def close(self):
        self.closed = True
        self.engine.disarm()
        self.requests.clear()
        self.observations.clear()

    def _error(self, exc):
        self.last_error = exc.reason
        self.counts['errors'] += 1
        return exc.code, b''

    def _execute(self, operation):
        try:
            return 0, operation() or b''
        except ReportError as exc:
            return self._error(exc)

    def _once(self, cache, key, fingerprint, operation):
        if key in cache:
            previous, result = cache[key]
            if previous != fingerprint:
                return self._error(ReportError('correlation_id_conflict'))
            cache.move_to_end(key)
            self.counts['duplicates'] += 1
            return result
        result = operation()
        cache[key] = fingerprint, result
        if len(cache) > self.cache_size:
            cache.popitem(last=False)
        return result

    def _write(self, rtype, number, packet):
        if self.closed:
            raise ReportError('transport_closed', errno.EPIPE)
        if not packet or packet[0] != number:
            raise ReportError('report_id_mismatch')
        if rtype == UHID_FEATURE_REPORT and number == 0x54:
            self.engine.write_feature(packet, number)
        elif rtype == UHID_OUTPUT_REPORT:
            self.engine.write_output(packet)
        else:
            raise ReportError('report_type_not_writable', errno.EOPNOTSUPP)
        self.counts['writes'] += 1

    def _read(self, rtype, number):
        if self.closed:
            raise ReportError('transport_closed', errno.EPIPE)
        if rtype == UHID_FEATURE_REPORT and number in (0x56, 0x57):
            data = self.engine.get_report(number)
        elif rtype == UHID_INPUT_REPORT and number == 0x50:
            data = self.engine.state_report()
        else:
            raise ReportError('report_type_not_readable', errno.EOPNOTSUPP)
        self.counts['reads'] += 1
        return data

    def _dispatch(self, rtype, number, packet, observation_id):
        if rtype not in (0, 1, 2):
            return self._error(ReportError('invalid_uhid_report_type'))
        operation = lambda: self._execute(lambda: self._write(rtype, number, packet))
        if observation_id is None:
            return operation()
        if not ((type(observation_id) is int and 0 <= observation_id < 2**64) or
                (type(observation_id) is str and 0 < len(observation_id) <= 128)):
            return self._error(ReportError('invalid_observation_id'))
        return self._once(self.observations, observation_id, (rtype, number, packet), operation)

    def handle(self, event, *, observation_id=None):
        """Retorna tupla de respostas prontas para uma futura camada de I/O.

        Inteiros UHID usam byte order nativa, sem padding ('='). Campos PID
        seguem little endian no decoder. Payload curto UHID é zero-estendido
        como prevê a UAPI; comprimentos dos relatórios PID continuam estritos.
        """
        self.last_error = None
        self.counts['events'] += 1
        if not isinstance(event, (bytes, bytearray, memoryview)) or len(event) < 4:
            self._error(ReportError('short_uhid_header'))
            return ()
        event = bytes(event)
        kind = struct.unpack_from('=I', event)[0]
        request_id = struct.unpack_from('=I', event, 4)[0] if len(event) >= 8 else None
        try:
            if len(event) > UHID_EVENT_SIZE:
                raise ReportError('oversized_uhid_event', errno.EMSGSIZE)
            if kind == UHID_CLOSE and not self.close_terminates:
                # CLOSE é o último leitor, não a destruição do UHID. Probes
                # podem abrir/fechar antes do jogo; não ressuscitar seus efeitos.
                self.engine.arm()
                self.requests.clear()
                self.observations.clear()
                return ()
            if kind in (UHID_STOP, UHID_CLOSE):
                self.close()
                return ()
            if kind in (2, 4):  # START/OPEN: não rearmam sessão encerrada.
                return ()
            if kind == UHID_OUTPUT:
                if len(event) < 4+UHID_DATA_MAX+3:
                    raise ReportError('short_uhid_output')
                size, rtype = struct.unpack_from('=HB', event, 4+UHID_DATA_MAX)
                if not 1 <= size <= UHID_DATA_MAX:
                    raise ReportError('invalid_uhid_size', errno.EMSGSIZE)
                packet = event[4:4+size]
                self._dispatch(rtype, packet[0], packet, observation_id)
                return ()
            if kind == UHID_SET_REPORT:
                if len(event) < 12:
                    raise ReportError('short_uhid_set_report')
                number, rtype, size = struct.unpack_from('=BBH', event, 8)
                if not 1 <= size <= UHID_DATA_MAX:
                    raise ReportError('invalid_uhid_size', errno.EMSGSIZE)
                packet = event[12:12+size].ljust(size, b'\0')
                result = self._once(
                    self.requests, (kind, request_id), (rtype, number, packet),
                    lambda: self._dispatch(rtype, number, packet, observation_id))
                return (struct.pack('=IIH', UHID_SET_REPORT_REPLY, request_id, result[0]),)
            if kind == UHID_GET_REPORT:
                if len(event) < 10:
                    raise ReportError('short_uhid_get_report')
                number, rtype = event[8:10]
                if rtype not in (0, 1, 2):
                    raise ReportError('invalid_uhid_report_type')
                err, packet = self._once(
                    self.requests, (kind, request_id), (rtype, number),
                    lambda: self._execute(lambda: self._read(rtype, number)))
                return (struct.pack('=IIHH', UHID_GET_REPORT_REPLY, request_id, err, len(packet)) + packet,)
            raise ReportError('unsupported_uhid_event', errno.EOPNOTSUPP)
        except ReportError as exc:
            err, _ = self._error(exc)
            if request_id is not None and kind == UHID_SET_REPORT:
                return (struct.pack('=IIH', UHID_SET_REPORT_REPLY, request_id, err),)
            if request_id is not None and kind == UHID_GET_REPORT:
                return (struct.pack('=IIHH', UHID_GET_REPORT_REPLY, request_id, err, 0),)
            return ()
