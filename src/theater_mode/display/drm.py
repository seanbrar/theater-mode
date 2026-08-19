"""Direct DRM connector detection via sysfs for low-latency output status checks."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from theater_mode.display.edid import OutputIdentity, parse_edid

DRM_DIR = Path("/sys/class/drm")


def _connectors() -> Iterator[tuple[str, Path, tuple[int, int] | None]]:
    """Yield (connector name, sysfs path, native mode) for connected DRM outputs."""
    for path in sorted(DRM_DIR.glob("card*-*")):
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
        name = path.name.partition("-")[2] or path.name

        width, _, height = first_mode.partition("x")
        try:
            size: tuple[int, int] | None = (int(width), int(height))
        except ValueError:
            size = None
        yield name, path, size


def connected_outputs() -> set[str]:
    """Return the connector names of all currently connected outputs."""
    return {name for name, _, _ in _connectors()}


def output_modes() -> dict[str, tuple[int, int]]:
    """Return the native mode (width, height) of every output that reports one."""
    return {name: size for name, _, size in _connectors() if size is not None}


def output_identities() -> dict[str, OutputIdentity]:
    """Return the EDID-derived identity of every connected output, keyed by connector."""
    identities: dict[str, OutputIdentity] = {}
    for name, path, _ in _connectors():
        try:
            blob = (path / "edid").read_bytes()
        except OSError:
            blob = b""
        identities[name] = parse_edid(name, blob)
    return identities
