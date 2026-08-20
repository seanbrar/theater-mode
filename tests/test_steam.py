"""Tests for Steam game detection and artwork handling."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from theater_mode.constants import IGNORED_CLASSES
from theater_mode.steam import (
    artwork_render_size,
    build_artwork,
    find_art_binary,
    find_hero_art,
    prune_artwork_cache,
    steam_appid_for_window,
)


class TestSteamDetection(unittest.TestCase):
    def test_steam_app_class_match(self) -> None:
        self.assertEqual(steam_appid_for_window("steam_app_12345", 100), "12345")

    def test_steam_game_id_in_environ(self) -> None:
        with patch(
            "theater_mode.steam.read_process_environ", return_value={"SteamGameId": "67890"}
        ):
            self.assertEqual(steam_appid_for_window("SomeGame", 200), "67890")

    def test_steam_appid_in_environ(self) -> None:
        with patch("theater_mode.steam.read_process_environ", return_value={"SteamAppId": "54321"}):
            self.assertEqual(steam_appid_for_window("SomeGame", 250), "54321")

    def test_gamescope_appid_in_cmdline(self) -> None:
        with (
            patch("theater_mode.steam.read_process_environ", return_value={}),
            patch(
                "theater_mode.steam.read_process_cmdline",
                return_value="gamescope --steam -e -- AppId=11111 /path/to/game",
            ),
        ):
            self.assertEqual(steam_appid_for_window("gamescope", 300), "11111")

    def test_ignored_classes(self) -> None:
        for ignored in (
            "plasmashell",
            "steamwebhelper",
            "steam",
            "xwaylandvideobridge",
            "org.kde.plasmashell",
        ):
            self.assertIn(ignored, IGNORED_CLASSES)
            self.assertIsNone(steam_appid_for_window(ignored, 400))

    def test_unidentified_window(self) -> None:
        with (
            patch("theater_mode.steam.read_process_environ", return_value={}),
            patch("theater_mode.steam.read_process_cmdline", return_value=""),
        ):
            self.assertIsNone(steam_appid_for_window("firefox", 500))

    def test_invalid_environ_or_cmdline_values(self) -> None:
        with patch(
            "theater_mode.steam.read_process_environ", return_value={"SteamGameId": "not_a_number"}
        ):
            self.assertIsNone(steam_appid_for_window("SomeGame", 600))
        with (
            patch("theater_mode.steam.read_process_environ", return_value={}),
            patch(
                "theater_mode.steam.read_process_cmdline", return_value="gamescope -- AppId=invalid"
            ),
        ):
            self.assertIsNone(steam_appid_for_window("gamescope", 700))


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

            with patch("theater_mode.steam.STEAM_LIBRARY_CACHES", (base,)):
                found = find_hero_art("12345")
                self.assertIsNotNone(found)
                self.assertEqual(found, art1)

    def test_find_hero_art_checks_native_and_flatpak_caches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            native = base / "native"
            flatpak = base / "flatpak"
            art = flatpak / "12345" / "hash" / "library_hero.jpg"
            art.parent.mkdir(parents=True)
            art.write_bytes(b"flatpak art")

            with patch("theater_mode.steam.STEAM_LIBRARY_CACHES", (native, flatpak)):
                self.assertEqual(find_hero_art("12345"), art)

    def test_find_art_binary_honours_its_env_override(self) -> None:
        """Resolution order is find_helper_binary's; this pins the name asked of it."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            fake_bin = Path(tmp_dir) / "theater-art"
            fake_bin.touch(mode=0o755)
            with patch.dict("os.environ", {"THEATER_ART_BIN": str(fake_bin)}):
                self.assertEqual(find_art_binary(), fake_bin)

    def test_build_artwork_missing_source_returns_none(self) -> None:
        with patch("theater_mode.steam.find_hero_art", return_value=None):
            result = build_artwork("99999", 1920, 1080, 0.4)
            self.assertIsNone(result)

    def test_build_artwork_missing_binary_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            hero = Path(tmp_dir) / "library_hero.jpg"
            hero.write_bytes(b"jpeg data")
            with (
                patch("theater_mode.steam.find_hero_art", return_value=hero),
                patch("theater_mode.steam.find_art_binary", return_value=None),
                patch("theater_mode.steam.ART_CACHE", Path(tmp_dir) / "cache"),
                self.assertLogs("theater-moded", level="WARNING"),
            ):
                self.assertIsNone(build_artwork("12345", 320, 240, 0.4))

    def test_build_artwork_subprocess_failure_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            hero = Path(tmp_dir) / "library_hero.jpg"
            hero.write_bytes(b"jpeg data")
            fake_bin = Path(tmp_dir) / "theater-art"
            fake_bin.touch(mode=0o755)

            with (
                patch("theater_mode.steam.find_hero_art", return_value=hero),
                patch("theater_mode.steam.find_art_binary", return_value=fake_bin),
                patch("theater_mode.steam.ART_CACHE", Path(tmp_dir) / "cache"),
                patch("subprocess.run", return_value=SimpleNamespace(returncode=1, stderr="error")),
                self.assertLogs("theater-moded", level="ERROR"),
            ):
                self.assertIsNone(build_artwork("12345", 320, 240, 0.4))

    def test_build_artwork_subprocess_timeout_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            hero = Path(tmp_dir) / "library_hero.jpg"
            hero.write_bytes(b"jpeg data")
            fake_bin = Path(tmp_dir) / "theater-art"
            fake_bin.touch(mode=0o755)

            with (
                patch("theater_mode.steam.find_hero_art", return_value=hero),
                patch("theater_mode.steam.find_art_binary", return_value=fake_bin),
                patch("theater_mode.steam.ART_CACHE", Path(tmp_dir) / "cache"),
                patch(
                    "subprocess.run",
                    side_effect=subprocess.TimeoutExpired(cmd="theater-art", timeout=3.0),
                ),
                self.assertLogs("theater-moded", level="ERROR"),
            ):
                self.assertIsNone(build_artwork("12345", 320, 240, 0.4))

    def test_build_artwork_generates_and_caches_argb(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            cache_dir = tmp_path / "cache"
            hero_file = tmp_path / "library_hero.jpg"
            hero_file.write_bytes(b"jpeg data")
            expected_target = cache_dir / "12345-v3-320x240-d0400.argb"
            fake_bin = tmp_path / "theater-art"
            fake_bin.touch(mode=0o755)

            def fake_run(cmd, **kwargs):
                out_path = Path(cmd[2])
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_bytes(b"\x00" * (320 * 240 * 4))
                return SimpleNamespace(returncode=0, stderr="")

            with (
                patch("theater_mode.steam.find_hero_art", return_value=hero_file),
                patch("theater_mode.steam.find_art_binary", return_value=fake_bin),
                patch("theater_mode.steam.ART_CACHE", cache_dir),
                patch("subprocess.run", side_effect=fake_run) as mock_run,
            ):
                target = build_artwork("12345", 320, 240, 0.4)
                self.assertIsNotNone(target)
                self.assertTrue(target.exists())
                self.assertEqual(target, expected_target)
                self.assertEqual(mock_run.call_count, 1)

                # Verify command invocation contract (including integer dim_millis)
                cmd = mock_run.call_args.args[0]
                self.assertEqual(cmd[0], str(fake_bin))
                self.assertEqual(cmd[1], str(hero_file))
                self.assertEqual(cmd[2], str(expected_target))
                self.assertEqual(cmd[3], "320")
                self.assertEqual(cmd[4], "240")
                self.assertEqual(cmd[5], "400")
                self.assertEqual(mock_run.call_args.kwargs.get("timeout"), 3.0)

                # Calling build_artwork again should return cached path without re-running subprocess
                cached_target = build_artwork("12345", 320, 240, 0.4)
                self.assertEqual(cached_target, target)
                self.assertEqual(mock_run.call_count, 1)

    def test_prune_artwork_cache_preserves_valid_variants_under_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            cache_dir = Path(tmp_dir)
            current = cache_dir / "12345-v3-1920x1080-d0850.argb"
            old_dim = cache_dir / "12345-v3-1920x1080-d0500.argb"
            other_app = cache_dir / "67890-v3-1920x1080-d0850.argb"
            for p in (current, old_dim, other_app):
                p.write_bytes(b"x" * 1000)

            prune_artwork_cache(cache_dir, current_target=current)
            self.assertTrue(current.exists())
            self.assertTrue(old_dim.exists())
            self.assertTrue(other_app.exists())

    def test_prune_artwork_cache_evicts_oldest_and_spares_current_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            cache_dir = Path(tmp_dir)
            files = [cache_dir / f"app{i}.argb" for i in (1, 2, 3)]
            for age, path in enumerate(files):
                path.write_bytes(b"x" * 500)
                os.utime(path, (1000 + age, 1000 + age))

            # The oldest entry is the one in use, so eviction has to skip it and take the
            # next oldest. Only this surviving set satisfies both rules at once.
            prune_artwork_cache(
                cache_dir, current_target=files[0], max_bytes=1200, trim_to_bytes=1100
            )
            self.assertEqual({p.name for p in cache_dir.glob("*.argb")}, {"app1.argb", "app3.argb"})

    def test_build_artwork_fail_soft_on_oserror(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            hero = Path(tmp_dir) / "library_hero.jpg"
            hero.write_bytes(b"jpeg data")
            fake_bin = Path(tmp_dir) / "theater-art"
            fake_bin.touch(mode=0o755)

            with (
                patch("theater_mode.steam.find_hero_art", return_value=hero),
                patch("theater_mode.steam.find_art_binary", return_value=fake_bin),
                patch("theater_mode.steam.ART_CACHE", Path(tmp_dir) / "cache"),
                patch("subprocess.run", side_effect=OSError("Disk full")),
                self.assertLogs("theater-moded", level="WARNING"),
            ):
                self.assertIsNone(build_artwork("12345", 320, 240, 0.4))


if __name__ == "__main__":
    unittest.main()
