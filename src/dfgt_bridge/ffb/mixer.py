# SPDX-License-Identifier: GPL-2.0-only
"""Soma ampla, ganho e um único teto final; reserva opcional para transientes."""
from dataclasses import dataclass
import math
from numbers import Real

from .effects import OUTPUT_COMPONENTS


CONDITIONS = ('spring', 'damper', 'friction', 'inertia')


@dataclass(frozen=True)
class MixResult:
    unlimited_requested: float
    requested: float
    limited: float
    clipped: bool
    condition_limited: bool


def mix_components(components, *, gain=1.0, limit=32767,
                   periodic_gain=1.0, condition_headroom=0.0):
    """Modo linear por padrão. Tudo é somado antes de limitar/quantizar.

    A reserva reduz apenas o agregado condicional, após seus cancelamentos;
    modifica a reprodução e precisa ser escolhida explicitamente pelo usuário.
    """
    try:
        if set(components) - set(OUTPUT_COMPONENTS):
            raise ValueError('Família de força desconhecida')
        if not all(isinstance(value, Real) for value in components.values()):
            raise ValueError('Força deve ser um número real')
        values = {key: float(components.get(key, 0)) for key in OUTPUT_COMPONENTS}
        if not all(math.isfinite(value) for value in values.values()):
            raise ValueError('Força não finita')
        if not all(math.isfinite(value) and value >= 0 for value in (
                gain, limit, periodic_gain)):
            raise ValueError('Ganho e teto devem ser finitos e não negativos')
        if not math.isfinite(condition_headroom) or not 0 <= condition_headroom <= 1:
            raise ValueError('Reserva condicional deve estar em 0..1')
        values['periodic'] *= periodic_gain
        unlimited = math.fsum(values.values()) * gain
        requested = unlimited
        condition_limited = False
        if condition_headroom:
            conditional = math.fsum(values[key] for key in CONDITIONS) * gain
            dynamic = math.fsum(values[key] for key in OUTPUT_COMPONENTS
                                if key not in CONDITIONS) * gain
            background_limit = limit * (1-condition_headroom)
            background = max(-background_limit, min(background_limit, conditional))
            condition_limited = background != conditional
            requested = math.fsum((background, dynamic))
        if not math.isfinite(unlimited) or not math.isfinite(requested):
            raise ValueError('Overflow na mistura de forças')
    except (OverflowError, TypeError) as exc:
        raise ValueError('Valores inválidos na mistura de forças') from exc
    limited = max(-limit, min(limit, requested))
    return MixResult(unlimited, requested, limited, limited != requested,
                     condition_limited)
