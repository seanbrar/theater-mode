"""KScreen / kscreen-doctor integration for brightness and display geometries."""

from __future__ import annotations

import json
import logging
import subprocess
from typing import Any

from theater_mode.display.base import OutputGeometry

log = logging.getLogger("theater-moded")


def query_kscreen_data() -> dict[str, Any]:
    """Execute kscreen-doctor -j to retrieve full display topology and state."""
    try:
        raw = subprocess.run(
            ["kscreen-doctor", "-j"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout
        return json.loads(raw)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        log.warning("failed to query kscreen display data: %s", exc)
        return {}


def output_brightness() -> dict[str, float | None]:
    """Return current per-output brightness (0.0 to 1.0), or None if unsupported.

    Outputs reporting None lack hardware/DDC brightness controls (or experienced
    a failed DDC handshake). These are identified and skipped gracefully.
    """
    data = query_kscreen_data()
    return {o["name"]: o.get("brightness") for o in data.get("outputs", []) if o.get("name")}


def output_positions() -> dict[str, tuple[int, int]]:
    """Return the top-left coordinate (x, y) for each connector name."""
    data = query_kscreen_data()
    positions: dict[str, tuple[int, int]] = {}
    for output in data.get("outputs", []):
        pos = output.get("pos") or {}
        name = output.get("name")
        if name and "x" in pos and "y" in pos:
            positions[name] = (int(pos["x"]), int(pos["y"]))
    return positions


def output_sizes() -> dict[str, tuple[int, int]]:
    """Return physical pixel dimensions (width, height) for each connector name."""
    data = query_kscreen_data()
    sizes: dict[str, tuple[int, int]] = {}
    for output in data.get("outputs", []):
        size = output.get("size") or {}
        name = output.get("name")
        if name and size.get("width") and size.get("height"):
            sizes[name] = (int(size["width"]), int(size["height"]))
    return sizes


def output_geometries() -> dict[str, OutputGeometry]:
    """Return complete geometry (position and size) for all connected outputs."""
    data = query_kscreen_data()
    geometries: dict[str, OutputGeometry] = {}
    for output in data.get("outputs", []):
        name = output.get("name")
        pos = output.get("pos") or {}
        size = output.get("size") or {}
        if name and "x" in pos and "y" in pos and size.get("width") and size.get("height"):
            geometries[name] = OutputGeometry(
                name=name,
                x=int(pos["x"]),
                y=int(pos["y"]),
                width=int(size["width"]),
                height=int(size["height"]),
            )
    return geometries


def set_output_brightness(levels: dict[str, int]) -> bool:
    """Set brightness for one or more outputs atomically using whole-number percentages.

    Design Notes:
    - Atomicity & Batching: All output adjustments are passed in a single kscreen-doctor
      invocation. This prevents staggered brightness adjustments across multi-monitor setups
      and minimizes display-configuration change events in KWin.
    - Silent Operation: Routed through kscreen-doctor rather than PowerDevil D-Bus
      interfaces to prevent on-screen display (OSD) popups over running games.
    - Whole-Number Values: Values are clamped integers (0-100). kscreen-doctor uses dots
      as command delimiters, so passing float values (e.g. '0.5') causes argument misparsing.
    """
    if not levels:
        return True

    args = [
        f"output.{output}.brightness.{max(0, min(100, int(percent)))}"
        for output, percent in sorted(levels.items())
    ]
    try:
        subprocess.run(
            ["kscreen-doctor", *args],
            capture_output=True,
            timeout=10,
            check=True,
        )
        return True
    except (OSError, subprocess.SubprocessError) as exc:
        log.error("failed to set brightness on %s: %s", ", ".join(sorted(levels)), exc)
        return False
