"""Unit tests for Steam game detection and artwork handling."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from theater_mode.steam import (
    _fast_feather_mask,
    artwork_render_size,
    build_artwork,
    find_hero_art,
    steam_appid_for_window,
)

try:
    import PIL  # noqa: F401

    HAS_PILLOW = True
except ImportError:
    HAS_PILLOW = False

# Pillow is an optional runtime dependency: without it secondary displays dim to plain
# black. These tests exercise the compositing itself, so they need the real library.
needs_pillow = unittest.skipUnless(HAS_PILLOW, "Pillow is not installed")


class TestSteamDetection(unittest.TestCase):
    def test_ignored_classes(self) -> None:
        self.assertIsNone(steam_appid_for_window("steam", 100))
        self.assertIsNone(steam_appid_for_window("steamwebhelper", 101))
        self.assertIsNone(steam_appid_for_window("plasmashell", 102))
        self.assertIsNone(steam_appid_for_window("org.kde.plasmashell", 103))
        self.assertIsNone(steam_appid_for_window("xwaylandvideobridge", 104))

    def test_steam_app_class_match(self) -> None:
        self.assertEqual(steam_appid_for_window("steam_app_1671210", 200), "1671210")
        self.assertEqual(steam_appid_for_window("steam_app_730", 201), "730")

    @patch("theater_mode.steam.read_process_environ")
    def test_steam_game_id_in_environ(self, mock_environ) -> None:
        mock_environ.return_value = {"SteamGameId": "123456"}
        self.assertEqual(steam_appid_for_window("SomeNativeGame", 300), "123456")

        mock_environ.return_value = {"SteamAppId": "654321"}
        self.assertEqual(steam_appid_for_window("AnotherGame", 301), "654321")

    @patch("theater_mode.steam.read_process_environ")
    @patch("theater_mode.steam.read_process_cmdline")
    def test_gamescope_appid_in_cmdline(self, mock_cmdline, mock_environ) -> None:
        mock_environ.return_value = {}
        mock_cmdline.return_value = (
            "gamescope -w 1920 -h 1080 -- reaper SteamLaunch AppId=1145360 -- proton run"
        )
        self.assertEqual(steam_appid_for_window("gamescope", 400), "1145360")

    def test_unidentified_window(self) -> None:
        with (
            patch("theater_mode.steam.read_process_environ", return_value={}),
            patch("theater_mode.steam.read_process_cmdline", return_value=""),
        ):
            self.assertIsNone(steam_appid_for_window("firefox", 500))


class TestArtwork(unittest.TestCase):
    def test_artwork_render_size_caps_dimensions_and_preserves_aspect_ratio(self) -> None:
        self.assertEqual(artwork_render_size(1920, 1080), (1920, 1080))
        self.assertEqual(artwork_render_size(3840, 2160), (1920, 1080))
        self.assertEqual(artwork_render_size(3440, 1440), (1920, 804))
        self.assertEqual(artwork_render_size(1080, 1920), (608, 1080))

    def test_find_hero_art_selection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            app_dir = base / "12345" / "subhash"
            app_dir.mkdir(parents=True)

            art1 = app_dir / "library_hero.jpg"
            art1.write_bytes(b"x" * 100)

            with patch("theater_mode.steam.STEAM_LIBRARY_CACHE", base):
                found = find_hero_art("12345")
                self.assertIsNotNone(found)
                self.assertEqual(found, art1)

    @needs_pillow
    def test_fast_feather_mask_gradient(self) -> None:
        width = 100
        fg_height = 80
        feather = 20

        mask = _fast_feather_mask(width, fg_height, feather)
        self.assertEqual(mask.size, (width, fg_height))
        self.assertEqual(mask.mode, "L")

        # Top should fade from 0 upwards
        self.assertEqual(mask.getpixel((0, 0)), 0)
        self.assertEqual(mask.getpixel((width - 1, 0)), 0)

        # Middle should be fully opaque (255)
        self.assertEqual(mask.getpixel((width // 2, fg_height // 2)), 255)

        # Bottom should fade down to ~0
        self.assertLessEqual(mask.getpixel((0, fg_height - 1)), 15)

        horizontal = _fast_feather_mask(width, fg_height, feather, horizontal=True)
        self.assertEqual(horizontal.getpixel((0, fg_height // 2)), 0)
        self.assertEqual(horizontal.getpixel((width // 2, fg_height // 2)), 255)
        self.assertEqual(horizontal.getpixel((width - 1, fg_height // 2)), 0)

    def test_build_artwork_missing_source_returns_none(self) -> None:
        with patch("theater_mode.steam.find_hero_art", return_value=None):
            result = build_artwork("99999", 1920, 1080, 0.4)
            self.assertIsNone(result)

    def test_build_artwork_without_pillow_degrades_instead_of_raising(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            hero = Path(tmp_dir) / "hero.png"
            hero.write_bytes(b"not really an image")
            with (
                patch.dict(sys.modules, {"PIL": None}),
                patch("theater_mode.steam.find_hero_art", return_value=hero),
                patch("theater_mode.steam.ART_CACHE", Path(tmp_dir) / "cache"),
                self.assertLogs("theater-moded", level="ERROR"),
            ):
                self.assertIsNone(build_artwork("12345", 320, 240, 0.4))

    @needs_pillow
    def test_build_artwork_generates_and_caches_argb(self) -> None:
        from PIL import Image

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            cache_dir = tmp_path / "cache"
            hero_file = tmp_path / "hero.jpg"
            expected_target = cache_dir / "12345-v2-320x240-d0400.argb"

            # Create a sample hero image
            img = Image.new("RGB", (1920, 620), color=(120, 140, 160))
            img.save(hero_file, quality=90)
            cache_dir.mkdir()
            expected_target.with_suffix(".tmp").write_bytes(b"interrupted write")

            with (
                patch("theater_mode.steam.find_hero_art", return_value=hero_file),
                patch("theater_mode.steam.ART_CACHE", cache_dir),
            ):
                target = build_artwork("12345", 320, 240, 0.4)
                self.assertIsNotNone(target)
                self.assertTrue(target.exists())
                self.assertEqual(target, expected_target)
                self.assertFalse(target.with_suffix(".tmp").exists())

                # Verify ARGB file size (320 * 240 * 4 bytes)
                expected_size = 320 * 240 * 4
                self.assertEqual(target.stat().st_size, expected_size)

                # Calling build_artwork again should return cached path without rewriting
                mtime_before = target.stat().st_mtime_ns
                cached_target = build_artwork("12345", 320, 240, 0.4)
                self.assertEqual(cached_target, target)
                self.assertEqual(target.stat().st_mtime_ns, mtime_before)

    @needs_pillow
    def test_build_artwork_crops_center_and_writes_bgra_pixels(self) -> None:
        from PIL import Image

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            cache_dir = tmp_path / "cache"
            hero_file = tmp_path / "hero.png"

            hero = Image.new("RGB", (80, 20), "green")
            hero.paste("red", (0, 0, 20, 20))
            hero.paste("blue", (60, 0, 80, 20))
            hero.save(hero_file)

            with (
                patch("theater_mode.steam.find_hero_art", return_value=hero_file),
                patch("theater_mode.steam.ART_CACHE", cache_dir),
            ):
                target = build_artwork("12345", 40, 40, 0.0)

            self.assertIsNotNone(target)
            rendered = Image.frombytes("RGBA", (40, 40), target.read_bytes(), "raw", "BGRA")

            # The square backdrop samples the green center of the wide source.
            backdrop_pixel = rendered.getpixel((20, 2))
            self.assertGreater(backdrop_pixel[1], backdrop_pixel[0])
            self.assertGreater(backdrop_pixel[1], backdrop_pixel[2])

            # The foreground retains the complete hero and is centered vertically.
            self.assertGreater(rendered.getpixel((2, 20))[0], 150)
            self.assertGreater(rendered.getpixel((37, 20))[2], 150)
            self.assertEqual(rendered.getpixel((20, 20))[3], 255)

    @needs_pillow
    def test_build_artwork_contains_portrait_art_and_feathers_its_sides(self) -> None:
        from PIL import Image

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            cache_dir = tmp_path / "cache"
            art_file = tmp_path / "box.png"

            art = Image.new("RGB", (20, 40), "green")
            art.paste("red", (0, 0, 20, 10))
            art.paste("blue", (0, 30, 20, 40))
            art.save(art_file)

            with (
                patch("theater_mode.steam.find_hero_art", return_value=art_file),
                patch("theater_mode.steam.ART_CACHE", cache_dir),
            ):
                target = build_artwork("12345", 40, 20, 0.0)

            self.assertIsNotNone(target)
            rendered = Image.frombytes("RGBA", (40, 20), target.read_bytes(), "raw", "BGRA")

            # Portrait art fits the display height, stays centered, and retains both ends.
            self.assertGreater(rendered.getpixel((20, 2))[0], 150)
            self.assertGreater(rendered.getpixel((20, 17))[2], 150)
            # Outside the portrait foreground, the green ambient backdrop remains visible.
            side = rendered.getpixel((2, 10))
            self.assertGreater(side[1], side[0])
            self.assertGreater(side[1], side[2])


if __name__ == "__main__":
    unittest.main()
