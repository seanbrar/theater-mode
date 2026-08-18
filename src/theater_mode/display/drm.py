"""Direct DRM connector detection via sysfs for low-latency output status checks."""

from __future__ import annotations

import glob
from collections.abc import Iterator
from pathlib import Path


def _connectors() -> Iterator[tuple[str, tuple[int, int] | None]]:
    """Yield (connector name, native mode) for connected DRM outputs from sysfs."""
    for connector in glob.glob("/sys/class/drm/card*-*"):
        path = Path(connector)
        try:
            if (path / "status").read_text().strip() != "connected":
                continue
        except OSError:
            continue

        try:
            first_mode = (path / "modes").read_text().split("\n", 1)[0].strip()
        except OSError:
            first_mode = ""

        # Convert sysfs directory name (e.g., card1-DP-1 or card0-HDMI-A-1) to connector name (DP-1)
        name = path.name.split("-", 1)[1] if "-" in path.name else path.name

        width, _, height = first_mode.partition("x")
        try:
            size: tuple[int, int] | None = (int(width), int(height))
        except ValueError:
            size = None
        yield name, size


def connected_outputs() -> set[str]:
    """Return the connector names of all currently connected outputs."""
    return {name for name, _ in _connectors()}


def output_modes() -> dict[str, tuple[int, int]]:
    """Return the native mode (width, height) of every output that reports one."""
    return {name: size for name, size in _connectors() if size is not None}
