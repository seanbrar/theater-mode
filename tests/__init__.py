"""Test suite for theater_mode package."""

from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import theater_mode  # noqa: E402, F401  (must follow the sys.path insert above)
