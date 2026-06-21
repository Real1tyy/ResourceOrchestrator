"""Mark `tests/` as a package so `tests._helpers` resolves.

Also adds `src/` to sys.path so unittest-discover doesn't depend on
pytest's conftest behavior — both runners can import `wt_cli.*`.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
