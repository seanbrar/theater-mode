"""Unit tests for effects pipeline and individual effect implementations."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from theater_mode.effects.brightness import BrightnessEffect
from theater_mode.effects.composite import CompositeEffect
from theater_mode.effects.log import LogEffect
from theater_mode.effects.wallpaper import WallpaperEffect


class TestLogEffect(unittest.TestCase):
    def test_log_effect(self) -> None:
        effect = LogEffect()
        self.assertEqual(effect.name, "log")
        effect.apply("DP-1", ["DP-2", "DP-3"], "1671210")
        effect.revert()
        self.assertIsNone(effect.saved_state())


class TestBrightnessEffect(unittest.TestCase):
    @patch("theater_mode.effects.brightness.output_brightness")
    @patch("theater_mode.effects.brightness.set_output_brightness")
    def test_brightness_baseline_and_dim(self, mock_set, mock_get) -> None:
        mock_get.return_value = {"DP-1": 0.8, "DP-2": 0.75, "DP-3": 0.60}

        effect = BrightnessEffect(dim_factor=0.20, settle_seconds=1.5)
        self.assertEqual(effect.name, "brightness")
        self.assertEqual(effect.transition_seconds, 1.5)

        # Apply theater mode on DP-1 (game on DP-1, secondary are DP-2, DP-3)
        effect.apply("DP-1", ["DP-2", "DP-3"], "1671210")

        # DP-2 baseline: 75 -> 15 (75 * 0.20), DP-3 baseline: 60 -> 12 (60 * 0.20)
        mock_set.assert_called_once_with({"DP-2": 15, "DP-3": 12})
        self.assertEqual(effect.saved_state(), {"brightness_percent": {"DP-2": 75, "DP-3": 60}})

        mock_set.reset_mock()

        # Revert
        effect.revert()
        mock_set.assert_called_once_with({"DP-2": 75, "DP-3": 60})
        self.assertIsNone(effect.saved_state())

    @patch("theater_mode.effects.brightness.set_output_brightness")
    def test_brightness_crash_recovery(self, mock_set) -> None:
        effect = BrightnessEffect(dim_factor=0.35)
        saved_state = {"brightness_percent": {"DP-2": 80, "DP-3": 75}}
        effect.recover(saved_state)
        mock_set.assert_called_once_with({"DP-2": 80, "DP-3": 75})


class TestWallpaperEffect(unittest.TestCase):
    @patch("theater_mode.effects.wallpaper.output_desktop_map")
    @patch("theater_mode.effects.wallpaper.output_sizes")
    @patch("theater_mode.effects.wallpaper.build_wallpaper")
    @patch("theater_mode.effects.wallpaper.read_wallpapers")
    @patch("theater_mode.effects.wallpaper.write_wallpapers")
    @patch("theater_mode.effects.wallpaper.restore_wallpapers")
    def test_wallpaper_apply_and_revert(
        self,
        mock_restore,
        mock_write,
        mock_read,
        mock_build,
        mock_sizes,
        mock_map,
    ) -> None:
        mock_map.return_value = {"DP-1": 0, "DP-2": 1}
        mock_sizes.return_value = {"DP-1": (3840, 2160), "DP-2": (1920, 1080)}
        mock_build.return_value = "/cache/art.jpg"
        mock_read.return_value = {1: ("org.waywallen.kde", "")}

        effect = WallpaperEffect()
        effect.apply("DP-1", ["DP-2"], "1671210")

        mock_write.assert_called_once_with({1: "/cache/art.jpg"})
        self.assertEqual(effect.saved_state(), {"wallpapers": {"DP-2": ["org.waywallen.kde", ""]}})

        effect.revert()
        mock_restore.assert_called_once_with({1: ("org.waywallen.kde", "")})
        self.assertIsNone(effect.saved_state())


class TestCompositeEffect(unittest.TestCase):
    def test_composite_composition(self) -> None:
        e1 = MagicMock(name="brightness", transition_seconds=1.5)
        e1.name = "brightness"
        e1.saved_state.return_value = {"val": 1}

        e2 = MagicMock(name="wallpaper", transition_seconds=0.0)
        e2.name = "wallpaper"
        e2.saved_state.return_value = {"val": 2}

        composite = CompositeEffect([e1, e2])
        self.assertEqual(composite.name, "brightness+wallpaper")
        self.assertEqual(
            composite.saved_state(), {"brightness": {"val": 1}, "wallpaper": {"val": 2}}
        )


if __name__ == "__main__":
    unittest.main()
