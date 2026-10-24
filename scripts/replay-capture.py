#!/usr/bin/env python3
"""Replay offline sem importar camadas de I/O de dispositivos."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from dfgt_bridge.replay import main

if __name__ == '__main__':
    raise SystemExit(main())
