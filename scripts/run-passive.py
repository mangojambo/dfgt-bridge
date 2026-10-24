#!/usr/bin/env python3
"""Entry point separado dos launchers e do nome legado dfgt_bridge.py."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from dfgt_bridge.runner import main

if __name__ == '__main__':
    raise SystemExit(main())
