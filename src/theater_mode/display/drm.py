"""Direct DRM connector detection via sysfs for low-latency output status checks."""

from __future__ import annotations

import glob
from pathlib import Path


def connected_outputs() -> set[str]:
    """Return the connector names of all currently connected outputs directly from DRM sysfs.

    Direct sysfs reads provide a near zero-overhead check, making it suitable to execute
    on every incoming window lifecycle event without IPC or process spawning delays.
    """
    outputs: set[str] = set()
    for connector in glob.glob("/sys/class/drm/card*-*"):
        path = Path(connector)
        try:
            status_file = path / "status"
            if not status_file.exists() or status_file.read_text().strip() != "connected":
                continue
        except OSError:
            continue

        # Convert sysfs directory name (e.g., card1-DP-1 or card0-HDMI-A-1) to connector name (DP-1)
        name = path.name
        if "-" in name:
            outputs.add(name.split("-", 1)[1])
        else:
            outputs.add(name)

    return outputs
