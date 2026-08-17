"""Utility functions for data conversion and Linux /proc process inspection."""

from __future__ import annotations

from pathlib import Path


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
    """Read a process's environment variables from /proc/<pid>/environ.

    Note: Proton/Wine processes running inside containerized runners (such as pressure-vessel)
    retain accessible /proc/<pid>/environ mappings in user sessions due to mount namespacing.
    """
    try:
        raw = Path(f"/proc/{pid}/environ").read_bytes()
    except (OSError, ValueError):
        return {}

    environ = {}
    for entry in raw.split(b"\0"):
        if not entry:
            continue
        key, _, value = entry.partition(b"=")
        environ[key.decode("utf-8", "replace")] = value.decode("utf-8", "replace")
    return environ
