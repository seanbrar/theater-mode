"""Test suite for theater_mode package."""

from __future__ import annotations

import contextlib
import sys
import warnings
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# Pre-import PyGObject namespaces with warning filter to suppress upstream override duplicate bug.
with warnings.catch_warnings():
    warnings.filterwarnings("ignore", message=r".*GLib\.unix_signal_add_full is deprecated.*")
    with contextlib.suppress(ImportError, ValueError):
        import gi

        gi.require_version("Gio", "2.0")
        gi.require_version("GLib", "2.0")
        gi.require_version("GLibUnix", "2.0")
        from gi.repository import Gio, GLib, GLibUnix  # noqa: F401

import theater_mode  # noqa: F401
