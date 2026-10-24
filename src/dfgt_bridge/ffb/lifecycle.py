# SPDX-License-Identifier: GPL-2.0-only
"""Timeline PID em tempo lógico: delay inicial, ciclos e duração por execução."""


INFINITE_LOOPS = 255  # Máximo do campo de 8 bits do descritor.


def start_effect(effect, now, loop_count):
    if not 0 <= loop_count <= 255:
        raise ValueError('Loop Count fora de u8')
    effect['started_at'] = now
    effect['loop_count'] = loop_count
    effect['loop_index'] = 0
    effect['running'] = loop_count != 0


def effect_time(effect, now):
    """Retorna tempo no ciclo atual, ou None se aguardando/parado.

    Atraso aplica-se uma vez após Start. Zero execuções não é infinito.
    Não percorre ciclos em um loop: saltos longos têm custo constante.
    """
    if not effect['running'] or effect['started_at'] is None:
        return None
    elapsed = round((now-effect['started_at']) * 1_000_000_000)
    elapsed -= effect['start_delay_ms'] * 1_000_000
    if elapsed < 0:
        return None
    duration = effect['duration_ms']
    if duration is None:
        return elapsed/1_000_000_000
    duration *= 1_000_000
    loops = effect['loop_count']
    if duration == 0 or (loops != INFINITE_LOOPS and elapsed >= loops*duration):
        effect['running'] = False
        return None
    index, elapsed = divmod(elapsed, duration)
    effect['loop_index'] = index
    return elapsed/1_000_000_000
