# SPDX-License-Identifier: GPL-2.0-only
"""Bounded statistics of successful software EVDEV commands, never torque."""
from collections import deque


WINDOW_NS = 5_000_000_000


class CommandStatistics:
    def __init__(self, gains=(2, 3, 4)):
        self.gains = gains
        self.buckets = tuple(str(gain) for gain in gains)+('transition', 'profile')
        self.totals = {key: [0, 0, 0, 0] for key in ('all', *self.buckets)}
        self.recent = deque(maxlen=2048)
        self.truncated_until_ns = 0

    @staticmethod
    def add(values, level, at_limit, clipped):
        values[0] += 1
        values[1] += int(at_limit)
        values[2] += int(clipped)
        values[3] = max(values[3], abs(level))

    def record(self, now_ns, level, cap, clipped, applied, selected):
        at_limit = cap > 0 and abs(level) == cap
        clipped = bool(level and clipped)
        bucket = ('transition' if applied != selected else
                  str(int(applied)) if applied in self.gains else 'profile')
        for key in ('all', bucket):
            self.add(self.totals[key], level, at_limit, clipped)
        if len(self.recent) == self.recent.maxlen:
            self.truncated_until_ns = self.recent[0][0]+WINDOW_NS
        self.recent.append((now_ns, level, at_limit, clipped, bucket))

    @staticmethod
    def describe(values):
        count, at_limit, clipped, peak = values
        return dict(commands=count, at_limit=at_limit, clipped=clipped,
                    at_limit_ratio=at_limit/count if count else None,
                    clipped_ratio=clipped/count if count else None, peak_abs_level=peak)

    def snapshot(self, now_ns):
        while self.recent and now_ns-self.recent[0][0] >= WINDOW_NS:
            self.recent.popleft()
        recent = {key: [0, 0, 0, 0] for key in ('all', *self.buckets)}
        for _, level, at_limit, clipped, bucket in self.recent:
            for key in ('all', bucket):
                self.add(recent[key], level, at_limit, clipped)
        return dict(metric='software_EVDEV_commands_not_torque',
                    session=self.describe(self.totals['all']),
                    session_by_spring={key: self.describe(self.totals[key]) for key in self.buckets},
                    recent=dict(window_ms=WINDOW_NS//1_000_000,
                        truncated=now_ns < self.truncated_until_ns,
                        **self.describe(recent['all']),
                        by_spring={key: self.describe(recent[key]) for key in self.buckets}))
