# SPDX-License-Identifier: GPL-2.0-only
"""Matemática pura dos efeitos; nenhuma dependência de dispositivos.

Convenções, escalas e aproximações: docs/FFB_MATH.md.
"""
import math
from dataclasses import dataclass


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


def signed_unit(value):
    """Signed HID endpoints: −32768→−1; +32767→+1; zero stays zero."""
    if not -32768 <= value <= 32767:
        raise ValueError('Campo signed fora de s16')
    return value / (32768.0 if value < 0 else 32767.0)


def unsigned_unit(value):
    if not 0 <= value <= 65535:
        raise ValueError('Campo unsigned fora de u16')
    return value / 65535.0


@dataclass(frozen=True)
class MotionState:
    position: float
    velocity: float = 0.0       # Posição normalizada por segundo.
    acceleration: float = 0.0   # Posição normalizada por segundo².

    def __post_init__(self):
        if not all(math.isfinite(v) for v in (
                self.position, self.velocity, self.acceleration)):
            raise ValueError('Movimento deve ser finito')


class MotionEstimator:
    """Estimativa no host; filtro e expiração são política, não constantes PID."""
    def __init__(self, filter_s=.045, stale_after_s=.25):
        if not (math.isfinite(filter_s) and filter_s >= 0 and
                math.isfinite(stale_after_s) and stale_after_s > 0):
            raise ValueError('Configuração inválida do estimador')
        self.filter_s = filter_s
        self.stale_after_s = stale_after_s
        self.reset()

    def reset(self):
        self.last_time = None
        self.last_position = 0.0
        self.velocity = self.acceleration = 0.0

    def sample(self, position_raw, now):
        if not math.isfinite(position_raw) or not math.isfinite(now):
            raise ValueError('Amostra de movimento deve ser finita')
        position = (min(16383, max(0, position_raw)) - 8191.5) / 8191.5
        if self.last_time is not None:
            dt = now - self.last_time
            if dt < 0:
                raise ValueError('Relógio do estimador recuou')
            # Coalesce timestamps indistinguíveis, sem derivadas gigantes.
            if dt < 1e-6:
                return MotionState(position, self.velocity, self.acceleration)
            if dt <= self.stale_after_s:
                instantaneous = (position - self.last_position) / dt
                alpha = dt / (self.filter_s + dt)
                previous_velocity = self.velocity
                self.velocity += alpha * (instantaneous - self.velocity)
                acceleration = (self.velocity - previous_velocity) / dt
                self.acceleration += alpha * (acceleration - self.acceleration)
            else:
                self.velocity = self.acceleration = 0.0
        self.last_time, self.last_position = now, position
        return MotionState(position, self.velocity, self.acceleration)


def condition_force(effect, motion, *, velocity_reference=2.0,
                    acceleration_reference=4.0):
    """Reação restauradora no eixo X; coeficientes negativos invertem a lei.

    Damper/Inertia usam escalas de referência explícitas do modelo em software.
    Friction é Coulomb: módulo constante fora do deadband, sem rampa viscosa.
    """
    if not all(math.isfinite(v) and v > 0 for v in (
            velocity_reference, acceleration_reference)):
        raise ValueError('Escalas de movimento devem ser finitas e positivas')
    p = effect_projection(effect)
    if p == 0:
        return 0.0
    kind = effect['kind']
    metric = {9: motion.position, 10: motion.velocity / velocity_reference,
              11: motion.acceleration / acceleration_reference,
              12: motion.velocity / velocity_reference}[kind]
    delta = p * metric - signed_unit(effect['center'])
    deadband = unsigned_unit(effect['deadband'])
    if delta > deadband:
        distance = delta - deadband
        coefficient = signed_unit(effect['coeff_pos'])
        saturation = unsigned_unit(effect['sat_pos'])
    elif delta < -deadband:
        distance = delta + deadband
        coefficient = signed_unit(effect['coeff_neg'])
        saturation = unsigned_unit(effect['sat_neg'])
    else:
        return 0.0
    # Uma métrica muito grande pode exceder float após sua normalização.
    # Zero coeficiente/saturação continuam exatamente zero, sem 0*inf→NaN.
    if coefficient == 0 or saturation == 0:
        return 0.0
    response = coefficient * (math.copysign(1.0, distance) if kind == 12 else distance)
    response = max(-saturation, min(saturation, response))
    return -response * p * FORCE_SCALE


def envelope_times(effect):
    # Mantém o domínio inteiro do relatório até a razão de interpolação.
    # Evita subtrações como 1.0-.2-.2 desviarem o arredondamento em 1 LSB.
    attack = effect['attack_time']
    duration = effect['duration_ms']
    fade = effect['fade_time'] if duration is not None else 0
    # PID não fornece uma regra única de sobreposição. Ajustamos as duas
    # transições proporcionalmente para uma curva contínua dentro da duração.
    if duration is not None and attack + fade > duration:
        ratio = duration / (attack + fade)
        attack, fade = attack * ratio, fade * ratio
    return attack, fade, duration


def envelope_magnitude(magnitude, effect, elapsed):
    """Níveis de ataque/fade são amplitudes absolutas, não fatores de ganho."""
    attack, fade, duration = envelope_times(effect)
    elapsed *= 1000
    if attack > 0 and elapsed < attack:
        fraction = elapsed / attack
        return effect['attack_level'] * (1-fraction) + magnitude * fraction
    if fade > 0 and elapsed >= duration-fade:
        fraction = min(1.0, (elapsed - (duration-fade)) / fade)
        return magnitude * (1-fraction) + effect['fade_level'] * fraction
    return magnitude


def enveloped_constant(level, effect, elapsed):
    # abs() representa a amplitude do envelope, jamais corrige coeficientes.
    sign = -1 if level < 0 else 1
    return sign * envelope_magnitude(abs(level), effect, elapsed)


def ramp_level(effect, elapsed):
    attack, fade, duration = envelope_times(effect)
    start, end = effect['ramp_start'], effect['ramp_end']
    if duration is None:
        return enveloped_constant(start, effect, elapsed)
    elapsed *= 1000
    sustain = max(0.0, duration-attack-fade)
    # Sem intervalo de sustain, usa um único ponto de encontro, sem salto
    # entre dois níveis incompatíveis. É política para envelope sobreposto.
    if sustain <= 1e-12:
        meeting = start + (end-start) * (attack/duration if duration else 0)
        start = end = meeting
    if attack > 0 and elapsed < attack:
        fraction = elapsed / attack
        return effect['attack_level'] * (1-fraction) + start * fraction
    if fade > 0 and elapsed >= duration-fade:
        fraction = min(1.0, (elapsed - (duration-fade))/fade)
        return end * (1-fraction) + effect['fade_level'] * fraction
    fraction = min(1.0, max(0.0, (elapsed-attack)/sustain)) if sustain else 0.0
    return start * (1-fraction) + end * fraction


def periodic_wave(kind, fraction):
    fraction %= 1.0
    if kind == 4:
        return 1.0 if fraction < .5 else -1.0
    if kind == 5:
        return math.sin(2*math.pi*fraction)
    if kind == 6:
        return 4*abs(fraction-.5)-1
    if kind == 7:
        return 2*fraction-1
    if kind == 8:
        return 1-2*fraction
    raise ValueError('Forma de onda PID desconhecida')
