#!/usr/bin/env python3
"""Local GUI; opening the window does not create UHID, captures, or motor output."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from dfgt_bridge.gui import main

if __name__ == '__main__':
    raise SystemExit(main())
