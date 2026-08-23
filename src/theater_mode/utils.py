"""Utility functions for data conversion, process inspection, and binary discovery."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from theater_mode.constants import LIBEXEC_DIR


def plural(count: int, singular: str, suffix: str = "s") -> str:
    """Render a count beside its noun, choosing the singular form at exactly one."""
    return f"{count} {singular}{'' if count == 1 else suffix}"


def parse_bool(value: str | bool) -> bool:
    """Parse string representation of boolean values safely."""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() == "true"


def parse_int(value: str | int, default: int = 0) -> int:
    """Parse integer values safely, returning default on invalid input."""
    if isinstance(value, int):
        return value
    try:
        return int(str(value).strip())
    except (ValueError, TypeError):
        return default


def read_process_cmdline(pid: int) -> str:
    """Read a process's command line from /proc/<pid>/cmdline as a single string.

    Returns an empty string if the process has exited or permission is denied.
    """
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except (OSError, ValueError):
        return ""
    return " ".join(part.decode("utf-8", "replace") for part in raw.split(b"\0") if part)


def read_process_environ(pid: int) -> dict[str, str]:
    """Read a process's environment from /proc, or return an empty mapping on failure."""
    try:
        raw = Path(f"/proc/{pid}/environ").read_bytes()
    except (OSError, ValueError):
        return {}

    environ = {}
    for entry in raw.split(b"\0"):
        if entry:
            key, _, value = entry.partition(b"=")
            environ[key.decode("utf-8", "replace")] = value.decode("utf-8", "replace")
    return environ


def find_helper_binary(name: str, env_var: str, subdir: str) -> Path | None:
    """Locate a compiled helper executable (from env override, package dir, libexec, or PATH)."""
    env_path = os.environ.get(env_var)
    if env_path and (p := Path(env_path)).is_file() and os.access(p, os.X_OK):
        return p

    pkg_bin = Path(__file__).parent / subdir / name
    if pkg_bin.is_file() and os.access(pkg_bin, os.X_OK):
        return pkg_bin

    libexec_bin = LIBEXEC_DIR / name
    if libexec_bin.is_file() and os.access(libexec_bin, os.X_OK):
        return libexec_bin

    which_bin = shutil.which(name)
    if which_bin:
        return Path(which_bin)

    return None
