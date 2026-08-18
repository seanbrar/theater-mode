"""Unit tests for display discovery via DRM sysfs."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from theater_mode.display.drm import connected_outputs, output_modes


class TestDisplayDRM(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)

    def connector(self, name: str, status: str, modes: str | None = "1920x1080\n1280x720\n") -> Path:
        path = self.base / name
        path.mkdir()
        (path / "status").write_text(f"{status}\n")
        if modes is not None:
            (path / "modes").write_text(modes)
        return path

    def patched_glob(self, *paths: Path):
        return patch("theater_mode.display.drm.glob.glob", return_value=[str(p) for p in paths])

    def test_only_connected_outputs_are_reported(self) -> None:
        dp1 = self.connector("card1-DP-1", "connected")
        dp2 = self.connector("card1-DP-2", "disconnected")
        hdmi = self.connector("card1-HDMI-A-1", "connected")

        with self.patched_glob(dp1, dp2, hdmi):
            self.assertEqual(connected_outputs(), {"DP-1", "HDMI-A-1"})

    def test_native_mode_is_the_first_listed(self) -> None:
        dp1 = self.connector("card1-DP-1", "connected", "3440x1440\n2560x1080\n1920x1080\n")

        with self.patched_glob(dp1):
            self.assertEqual(output_modes(), {"DP-1": (3440, 1440)})

    def test_an_output_with_no_usable_mode_still_counts_as_connected(self) -> None:
        dp1 = self.connector("card1-DP-1", "connected", "")
        dp2 = self.connector("card1-DP-2", "connected", "not-a-mode\n")

        with self.patched_glob(dp1, dp2):
            self.assertEqual(connected_outputs(), {"DP-1", "DP-2"})
            self.assertEqual(output_modes(), {})

    def test_a_connector_without_a_modes_file_still_gets_dimmed(self) -> None:
        dp1 = self.connector("card1-DP-1", "connected", modes=None)

        with self.patched_glob(dp1):
            self.assertEqual(connected_outputs(), {"DP-1"})
            self.assertEqual(output_modes(), {})


if __name__ == "__main__":
    unittest.main()
