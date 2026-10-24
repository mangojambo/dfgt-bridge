# SPDX-License-Identifier: GPL-2.0-only
"""Matemática pura dos efeitos; nenhuma dependência de dispositivos.

Convenções, escalas e aproximações: docs/FFB_MATH.md.
"""
import math


FORCE_SCALE = 32767
ANGLE_MAX = 35900  # Descritor preservado: centésimos de grau.


def direction_x(angle):
    """Componente X polar, no mesmo sentido algébrico de sin() em lg4ff.

    O ângulo já está em unidades HID, não em unidades Linux 0..65535.
    Pontos cardinais são exatos para não deixar resíduos de força no eixo X.
    """
    if not math.isfinite(angle) or not 0 <= angle <= ANGLE_MAX:
        raise ValueError('Direção fora do descritor HID: 0..35900')
    cardinal = {0: 0.0, 9000: 1.0, 18000: 0.0, 27000: -1.0}
    if angle in cardinal:
        return cardinal[angle]
    return math.sin(math.radians(angle / 100.0))


def effect_projection(effect):
    if effect['direction_enabled']:
        return direction_x(effect['direction'])
    return 1.0 if effect['axes_enabled'] & 1 else 0.0
