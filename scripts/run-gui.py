#!/usr/bin/env python3
"""GUI local; abrir a janela não cria UHID, captura nem saída de motor."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from dfgt_bridge.gui import main

if __name__ == '__main__':
    raise SystemExit(main())
