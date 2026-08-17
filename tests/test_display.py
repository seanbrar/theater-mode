"""Unit tests for display discovery, geometry mapping, and brightness control."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from theater_mode.display.base import OutputGeometry
from theater_mode.display.drm import connected_outputs
from theater_mode.display.kscreen import (
    output_brightness,
    output_geometries,
    output_positions,
    output_sizes,
    set_output_brightness,
)
from theater_mode.display.plasma import (
    output_desktop_map,
    read_wallpapers,
)


class TestDisplayDRM(unittest.TestCase):
    def test_connected_outputs_parsing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            card_dp1 = base / "card1-DP-1"
            card_dp2 = base / "card1-DP-2"
            card_hdmi = base / "card1-HDMI-A-1"

            card_dp1.mkdir()
            (card_dp1 / "status").write_text("connected\n")

            card_dp2.mkdir()
            (card_dp2 / "status").write_text("disconnected\n")

            card_hdmi.mkdir()
            (card_hdmi / "status").write_text("connected\n")

            with patch("theater_mode.display.drm.glob.glob") as mock_glob:
                mock_glob.return_value = [str(card_dp1), str(card_dp2), str(card_hdmi)]
                connected = connected_outputs()
                self.assertEqual(connected, {"DP-1", "HDMI-A-1"})


class TestKScreen(unittest.TestCase):
    @patch("theater_mode.display.kscreen.query_kscreen_data")
    def test_output_brightness(self, mock_query) -> None:
        mock_query.return_value = {
            "outputs": [
                {"name": "DP-1", "brightness": 0.8},
                {"name": "DP-2", "brightness": None},
                {"name": "DP-3", "brightness": 0.5},
            ]
        }
        res = output_brightness()
        self.assertEqual(res, {"DP-1": 0.8, "DP-2": None, "DP-3": 0.5})

    @patch("theater_mode.display.kscreen.query_kscreen_data")
    def test_output_geometries(self, mock_query) -> None:
        mock_query.return_value = {
            "outputs": [
                {
                    "name": "DP-1",
                    "pos": {"x": 0, "y": 0},
                    "size": {"width": 3840, "height": 2160},
                },
                {
                    "name": "DP-2",
                    "pos": {"x": 3840, "y": 0},
                    "size": {"width": 1920, "height": 1080},
                },
            ]
        }
        positions = output_positions()
        self.assertEqual(positions, {"DP-1": (0, 0), "DP-2": (3840, 0)})

        sizes = output_sizes()
        self.assertEqual(sizes, {"DP-1": (3840, 2160), "DP-2": (1920, 1080)})

        geoms = output_geometries()
        self.assertIn("DP-1", geoms)
        self.assertEqual(geoms["DP-1"], OutputGeometry("DP-1", 0, 0, 3840, 2160))

    @patch("theater_mode.display.kscreen.subprocess.run")
    def test_set_output_brightness_formatting(self, mock_run) -> None:
        mock_run.return_value = MagicMock(returncode=0)
        success = set_output_brightness({"DP-2": 25, "DP-3": 15})
        self.assertTrue(success)

        called_args = mock_run.call_args[0][0]
        self.assertEqual(
            called_args,
            ["kscreen-doctor", "output.DP-2.brightness.25", "output.DP-3.brightness.15"],
        )


class TestPlasmaBridge(unittest.TestCase):
    @patch("theater_mode.display.plasma.plasma_evaluate")
    @patch("theater_mode.display.plasma.output_positions")
    def test_output_desktop_map(self, mock_positions, mock_eval) -> None:
        mock_eval.return_value = "0:0,0;1:3840,0"
        mock_positions.return_value = {"DP-1": (0, 0), "DP-2": (3840, 0)}

        mapping = output_desktop_map()
        self.assertEqual(mapping, {"DP-1": 0, "DP-2": 1})

    @patch("theater_mode.display.plasma.plasma_evaluate")
    def test_read_wallpapers_batching(self, mock_eval) -> None:
        mock_eval.return_value = "0\torg.kde.image\tfile:///test1.jpg;1\torg.waywallen.kde\t"
        res = read_wallpapers([0, 1])
        self.assertEqual(res[0], ("org.kde.image", "file:///test1.jpg"))
        self.assertEqual(res[1], ("org.waywallen.kde", ""))


if __name__ == "__main__":
    unittest.main()
