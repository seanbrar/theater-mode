"""Unit tests for display discovery via DRM sysfs."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_edid import build_edid
from theater_mode.display.drm import connected_outputs, output_identities, output_modes


class TestDisplayDRM(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        patcher = patch("theater_mode.display.drm.DRM_DIR", self.base)
        patcher.start()
        self.addCleanup(patcher.stop)

    def connector(
        self,
        name: str,
        status: str,
        modes: str | None = "1920x1080\n1280x720\n",
        edid: bytes | None = None,
    ) -> Path:
        path = self.base / name
        path.mkdir()
        (path / "status").write_text(f"{status}\n")
        if modes is not None:
            (path / "modes").write_text(modes)
        if edid is not None:
            (path / "edid").write_bytes(edid)
        return path

    def test_only_connected_outputs_are_reported(self) -> None:
        self.connector("card1-DP-1", "connected")
        self.connector("card1-DP-2", "disconnected")
        self.connector("card1-HDMI-A-1", "connected")

        self.assertEqual(connected_outputs(), {"DP-1", "HDMI-A-1"})

    def test_native_mode_is_the_first_listed(self) -> None:
        self.connector("card1-DP-1", "connected", "3440x1440\n2560x1080\n1920x1080\n")

        self.assertEqual(output_modes(), {"DP-1": (3440, 1440)})

    def test_an_output_with_no_usable_mode_still_counts_as_connected(self) -> None:
        self.connector("card1-DP-1", "connected", "")
        self.connector("card1-DP-2", "connected", "not-a-mode\n")

        self.assertEqual(connected_outputs(), {"DP-1", "DP-2"})
        self.assertEqual(output_modes(), {})

    def test_a_connector_without_a_modes_file_still_gets_dimmed(self) -> None:
        self.connector("card1-DP-1", "connected", modes=None)

        self.assertEqual(connected_outputs(), {"DP-1"})
        self.assertEqual(output_modes(), {})

    def test_identities_are_read_from_connector_edid(self) -> None:
        self.connector("card1-DP-1", "connected", edid=build_edid(monitor_name="U3419W"))
        self.connector("card1-DP-2", "connected")  # no edid file at all

        identities = output_identities()

        self.assertEqual(identities["DP-1"].model, "U3419W")
        self.assertIn("DEL:U3419W:4QCPZY3", identities["DP-1"].match_keys)

        # A connector without readable EDID is still reported, matchable by name.
        self.assertEqual(identities["DP-2"].match_keys, ())
        self.assertEqual(identities["DP-2"].connector, "DP-2")

    def test_identical_panels_are_separated_by_serial(self) -> None:
        self.connector("card1-DP-2", "connected", edid=build_edid(serial_text="AAA1111"))
        self.connector("card1-DP-3", "connected", edid=build_edid(serial_text="BBB2222"))

        identities = output_identities()

        self.assertNotEqual(identities["DP-2"].match_keys[0], identities["DP-3"].match_keys[0])
        # ...but the less specific make:model key is shared by both.
        self.assertEqual(identities["DP-2"].match_keys[-1], identities["DP-3"].match_keys[-1])


if __name__ == "__main__":
    unittest.main()
