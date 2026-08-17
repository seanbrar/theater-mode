"""Unit tests for Steam game detection and artwork handling."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from theater_mode.steam import (
    find_hero_art,
    steam_appid_for_window,
)


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
        with patch("theater_mode.steam.read_process_environ", return_value={}):
            with patch("theater_mode.steam.read_process_cmdline", return_value=""):
                self.assertIsNone(steam_appid_for_window("firefox", 500))


class TestArtwork(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
