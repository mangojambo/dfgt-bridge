# SPDX-License-Identifier: GPL-2.0-only
"""Supervisão readonly de DiRT5.exe; identidade inclui starttime do /proc."""
from pathlib import Path


class GameMonitor:
    def __init__(self, pid=None, *, root='/proc'):
        self.root = Path(root)
        self.requested = pid
        self.identity = None
        self.last_scan_ns = -1_000_000_000

    def identity_for(self, pid):
        try:
            base = self.root / str(pid)
            if base.joinpath('comm').read_text().strip().casefold() not in ('dirt5.exe', 'dirt5'):
                return None
            fields = base.joinpath('stat').read_text().rsplit(')', 1)[1].split()
            if fields[0] in ('Z', 'X'):
                return None
            return int(pid), int(fields[19])  # campo 22, starttime; evita reutilização de PID.
        except (OSError, ValueError, IndexError):
            return None

    def poll(self, now_ns):
        if self.identity is not None:
            if self.identity_for(self.identity[0]) == self.identity:
                return self.identity
            self.identity = None
            # Informar primeiro a saída; nova descoberta só numa próxima chamada.
            return None
        if self.requested is not None:
            self.identity = self.identity_for(self.requested)
            return self.identity
        if now_ns-self.last_scan_ns >= 500_000_000:
            self.last_scan_ns = now_ns
            try:
                candidates = self.root.iterdir()
                for path in candidates:
                    if path.name.isdecimal():
                        found = self.identity_for(path.name)
                        if found:
                            self.identity = found
                            break
            except OSError:
                pass
        return self.identity
