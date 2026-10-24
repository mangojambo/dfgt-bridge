# SPDX-License-Identifier: GPL-2.0-only
"""Composição offline transporte→estado→render→mock. Nenhuma camada de I/O."""
import time

from ..backends.mock import MockBackend
from .effects import PIDSteeringBridge
from .safety import ConstantOutput
from .transport import PIDTransport


class OfflineSession:
    def __init__(self, clock=time.monotonic, *, capacity=40, strength_percent=20,
                 backend=None, cache_size=128):
        backend = MockBackend(clock=clock) if backend is None else backend
        if not isinstance(backend, MockBackend):
            raise ValueError('Sessão offline aceita exclusivamente MockBackend')
        self.engine = PIDSteeringBridge(clock, capacity=capacity)
        self.transport = PIDTransport(self.engine, cache_size)
        self.output = ConstantOutput(backend, strength_percent, clock=clock)
        self.position_raw = 8192
        self.closed = False
        self.engine.arm()

    def handle(self, event, *, observation_id=None):
        try:
            replies = self.transport.handle(event, observation_id=observation_id)
            if self.transport.closed:
                self.close()
            elif not self.closed:
                self.tick()
            return replies
        except BaseException:
            self.close()
            raise

    def tick(self, position_raw=None, *, motion=None):
        if self.closed:
            return
        if position_raw is not None:
            self.position_raw = position_raw
        try:
            self.output.tick(self.engine.components(self.position_raw, motion=motion))
        except BaseException:
            self.close()
            raise

    def close(self):
        if not self.closed:
            self.closed = True
            try:
                self.transport.close()
            finally:
                self.output.close()

    def __enter__(self):
        if self.closed:
            raise ValueError('Sessão encerrada')
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
        return False
