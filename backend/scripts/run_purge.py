"""정기 파기 실행: `python -m scripts.run_purge` (설명은 app/interface/cli/purge.py)"""

from __future__ import annotations

import sys

from app.interface.cli.purge import main

if __name__ == "__main__":
    sys.exit(main())
