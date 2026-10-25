# SPDX-License-Identifier: GPL-2.0-only
"""Bootstrap confiável instalado; o launcher jamais recebe execução privilegiada."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if __name__ == '__main__':
    if sys.argv[1:2] == ['--daemon']:
        from dfgt_bridge.steam_service import main
        main()
    else:
        from dfgt_bridge.steam_launcher import main
        raise SystemExit(main())
