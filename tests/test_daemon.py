"""Unit tests for Daemon state machine and window tracking."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from theater_mode.daemon import Daemon


class TestDaemon(unittest.TestCase):
    def setUp(self) -> None:
        self.mock_effect = MagicMock()
        self.mock_effect.name = "log"

        self.daemon = Daemon(
            effect=self.mock_effect,
            require_fullscreen=False,
            revert_delay=0.0,
            stage_delay=0.0,
        )

    @patch("theater_mode.daemon.connected_outputs", return_value={"DP-1", "DP-2", "DP-3"})
    def test_non_game_window(self, _) -> None:
        self.daemon.window_opened("win-1", "firefox", "100", "DP-1", "false", "true")
        self.assertEqual(len(self.daemon.windows), 1)
        self.assertIsNone(self.daemon.active_output)
        self.mock_effect.apply.assert_not_called()

    @patch("theater_mode.daemon.connected_outputs", return_value={"DP-1", "DP-2", "DP-3"})
    def test_game_window_open_and_close(self, _) -> None:
        self.daemon.window_opened("win-game", "steam_app_1671210", "200", "DP-1", "true", "true")
        self.assertEqual(self.daemon.active_output, "DP-1")
        self.mock_effect.apply.assert_called_once_with("DP-1", ["DP-2", "DP-3"], "1671210")

        self.daemon.window_closed("win-game")
        self.assertIsNone(self.daemon.active_output)
        self.mock_effect.revert.assert_called_once()

    @patch("theater_mode.daemon.connected_outputs", return_value={"DP-1", "DP-2"})
    def test_snapshot_reconciliation(self, _) -> None:
        self.daemon.window_opened("win-1", "steam_app_100", "300", "DP-1", "true")
        self.daemon.window_opened("win-2", "steam_app_200", "301", "DP-2", "true")
        self.assertEqual(len(self.daemon.windows), 2)

        # Snapshot containing only win-1
        self.daemon.snapshot_begin()
        self.daemon.window_opened("win-1", "steam_app_100", "300", "DP-1", "true")
        self.daemon.snapshot_end()

        self.assertEqual(list(self.daemon.windows.keys()), ["win-1"])

    def test_display_hotplug_updates_effect(self) -> None:
        with patch("theater_mode.daemon.connected_outputs", return_value={"DP-1", "DP-2"}):
            self.daemon.window_opened("win-game", "steam_app_1671210", "200", "DP-1", "true")
            self.mock_effect.apply.assert_called_once_with("DP-1", ["DP-2"], "1671210")

        # DP-3 connected while game is running
        with patch("theater_mode.daemon.connected_outputs", return_value={"DP-1", "DP-2", "DP-3"}):
            self.daemon.reconcile()

        self.assertEqual(
            self.mock_effect.apply.call_args.args, ("DP-1", ["DP-2", "DP-3"], "1671210")
        )
        self.assertEqual(self.daemon.active_output, "DP-1")

    @patch("theater_mode.daemon.connected_outputs", return_value={"DP-1", "DP-2"})
    def test_unchanged_display_set_does_not_reapply(self, _) -> None:
        self.daemon.window_opened("win-game", "steam_app_1671210", "200", "DP-1", "true")
        self.daemon.reconcile()
        self.daemon.reconcile()
        self.mock_effect.apply.assert_called_once()

    @patch("theater_mode.daemon.connected_outputs", return_value={"DP-1", "DP-2"})
    @patch("theater_mode.daemon.steam_appid_for_window", return_value="1671210")
    def test_repeat_announcements_cache_proc_inspection(self, mock_probe, _) -> None:
        for _ in range(3):
            self.daemon.window_opened("win-game", "steam_app_1671210", "200", "DP-1", "true")
        mock_probe.assert_called_once()

        # Recycled window id with new pid triggers new inspection
        self.daemon.window_opened("win-game", "steam_app_1671210", "999", "DP-1", "true")
        self.assertEqual(mock_probe.call_count, 2)

    @patch("theater_mode.daemon.connected_outputs", return_value={"DP-1", "DP-2"})
    def test_simulate_and_clear(self, _) -> None:
        res = self.daemon.simulate("1671210", "DP-1")
        self.assertIn("simulated game", res)
        self.assertEqual(self.daemon.active_output, "DP-1")

        clear_res = self.daemon.clear(immediate=True)
        self.assertEqual(clear_res, "cleared")
        self.assertIsNone(self.daemon.active_output)
        self.assertEqual(len(self.daemon.windows), 0)


if __name__ == "__main__":
    unittest.main()
