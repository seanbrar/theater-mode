"""Test suite for theater_mode package."""

import sys
from pathlib import Path

# Ensure src/ is on sys.path when running tests.
SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import theater_mode  # noqa: F401
