# SPDX-License-Identifier: GPL-2.0-only
# Extraído da v0.13 preservada em reference/v0.13/.
"""Política de saída Constant injetável; este módulo não abre hardware."""
from collections import Counter
from dataclasses import dataclass
import math
import time

from .effects import OUTPUT_COMPONENTS
from .mixer import mix_components
from ..backends.mock import MockBackend

EV_FF = 0x15
FF_CONSTANT = 0x52


@dataclass(frozen=True)
class ConstantForce:
    level: int
    effect_id: int
    duration_ms: int
    direction: int = 0x4000


class ConstantOutput:
    """Um slot; taxa normal limitada. Redução de teto e stop têm prioridade.

    Produz pedidos de replay de 160 ms; o mock apenas os registra. A garantia
    de expiração de um futuro adaptador físico precisa de validação própria.
    """
    def __init__(self, dev=None, strength_percent=12, rate_hz=45, clock=time.monotonic,
                 boost=1.0, periodic_boost=1.0, max_strength_percent=60,
                 test_limit_s=120, condition_headroom=0.0):
        dev = MockBackend() if dev is None else dev
        if (dev.info.vendor, dev.info.product) != (0x046d, 0xc29a):
            raise RuntimeError('FFB rejeitado: fonte física não é DFGT 046d:c29a')
        if FF_CONSTANT not in dev.capabilities().get(EV_FF, []):
            raise RuntimeError('FFB rejeitado: DFGT físico não suporta FF_CONSTANT')
        if not (0 <= strength_percent <= max_strength_percent and
                5 <= max_strength_percent <= 70 and 15 <= rate_hz <= 45):
            raise ValueError('Limites: intensidade inicial 0..máximo, teto 5..70%, taxa 15..45Hz')
        if not (30 <= test_limit_s <= 240):
            raise ValueError('Duração de teste permitida: 30..240s')
        if not math.isfinite(boost) or not 1.0 <= boost <= 12.0:
            raise ValueError('Boost precisa estar entre 1.0 e 12.0')
        if not math.isfinite(condition_headroom) or not 0 <= condition_headroom <= 1:
            raise ValueError('Reserva condicional deve estar em 0..1')
        if not math.isfinite(periodic_boost) or not 0.0 <= periodic_boost <= 3.0:
            raise ValueError('Periodic boost 0..3')
        self.dev = dev
        self.clock = clock
        self.boost = boost
        self.condition_headroom = condition_headroom
        self.periodic_boost = periodic_boost
        self.component_stats = Counter()
        self.strength_percent = strength_percent
        self.max_strength_percent = max_strength_percent
        self.cap = round(32767 * strength_percent / 100)
        self.enabled = True
        self.interval = 1.0 / rate_hz
        self.id = -1
        self.last_tick = -1e9
        self.last_sent = 0
        self.playing = False
        self.writes = 0
        self.peak = 0
        self.peak_input = 0
        self.saturation_ticks = 0
        self.last_mix = None
        self.condition_limited_ticks = 0
        self.deadline_ms = 160  # Prazo do pedido; independente da duração PID.
        self.first_active = None
        self.expired = False
        self.test_limit_s = test_limit_s  # Bounded supervised run.

    def set_strength(self, new_percent):
        self.strength_percent = max(0, min(self.max_strength_percent, int(new_percent)))
        self.cap = round(32767 * self.strength_percent / 100)
        # Não esperar o próximo tick nem atravessar o slew para obedecer a um
        # teto menor. Esta operação pode antecipar uma atualização normal.
        if abs(self.last_sent) > self.cap:
            self._send_level(max(-self.cap, min(self.cap, self.last_sent)))
        return self.strength_percent

    def set_enabled(self, enabled):
        self.enabled = bool(enabled)
        if not self.enabled:
            self.stop()
        return self.enabled

    def _effect(self, level):
        return ConstantForce(level=level, effect_id=self.id, duration_ms=self.deadline_ms)

    def tick(self, normalized_level):
        now = self.clock()
        if not math.isfinite(now):
            self.set_enabled(False)
            raise ValueError('Relógio de saída deve ser finito')
        if not self.enabled:
            self.stop()
            return
        if self.expired or (self.first_active is not None and
                            now - self.first_active >= self.test_limit_s):
            if not self.expired:
                print(f'[FFB físico] Limite de {self.test_limit_s}s atingido; motor parado. '
                      'Encerre e reinicie o script para novo teste.', flush=True)
            self.expired = True
            self.stop()
            return
        components = normalized_level if isinstance(normalized_level, dict) else {'constant': normalized_level}
        try:
            self.last_mix = mix_components(
                components, gain=self.boost * self.cap / 32767, limit=self.cap,
                periodic_gain=self.periodic_boost,
                condition_headroom=self.condition_headroom)
        except ValueError:
            self.set_enabled(False)
            raise
        for key in OUTPUT_COMPONENTS:
            self.component_stats[key] = max(self.component_stats[key], abs(components.get(key, 0)))
        game_magnitude = math.fsum(components.values())
        level = round(self.last_mix.limited)
        if level == 0:
            self.stop()  # Zero/Reset/Stop não esperam o gate de taxa.
            return
        if now - self.last_tick < self.interval:
            return
        self.last_tick = now
        self.peak_input = max(self.peak_input, abs(game_magnitude))
        if self.last_mix.clipped:
            self.saturation_ticks += 1
        if self.last_mix.condition_limited:
            self.condition_limited_ticks += 1
        if self.first_active is None:
            self.first_active = now
        # Limit change in output per tick; avoids a sudden full reversal.
        step = max(512, self.cap // 2)
        if level > self.last_sent + step:
            level = self.last_sent + step
        elif level < self.last_sent - step:
            level = self.last_sent - step
        # O slew jamais tem prioridade sobre o teto final, inclusive quando
        # a intensidade foi reduzida ou o último valor veio de outro perfil.
        self._send_level(max(-self.cap, min(self.cap, level)))

    def _send_level(self, level):
        if level == 0:
            self.stop()
            return
        now = self.clock()
        if not math.isfinite(now):
            self.set_enabled(False)
            raise ValueError('Relógio de saída deve ser finito')
        new_id = self.dev.upload_effect(self._effect(level))
        if self.id >= 0 and new_id != self.id:
            raise RuntimeError('Driver não reutilizou slot FFB; encerrando por segurança')
        self.id = new_id
        self.dev.write(EV_FF, self.id, 1)
        self.dev.syn()
        self.playing = True
        self.last_sent = level
        self.peak = max(self.peak, abs(level))
        self.writes += 1
        self.last_tick = now

    def stop(self):
        # No repeated zero commands: avoid burdening the Logitech driver.
        if self.playing and self.id >= 0:
            self.dev.write(EV_FF, self.id, 0)
            self.dev.syn()
        self.playing = False
        self.last_sent = 0

    def close(self):
        try:
            self.stop()
        finally:
            if self.id >= 0:
                try:
                    self.dev.erase_effect(self.id)
                finally:
                    self.id = -1
