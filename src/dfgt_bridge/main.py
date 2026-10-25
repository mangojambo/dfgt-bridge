# SPDX-License-Identifier: GPL-2.0-only
"""Bootstrap absoluto usado pela GUI, inclusive em instalação de pacote."""
from pathlib import Path
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dfgt_bridge.runner import main

if __name__ == '__main__':
    raise SystemExit(main())
