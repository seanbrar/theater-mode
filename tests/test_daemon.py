"""Unit tests for Daemon state machine and window tracking lifecycle."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from theater_mode.daemon import Daemon


class TestDaemon(unittest.TestCase):
    def setUp(self) -> None:
        self.mock_effect = MagicMock()
        self.mock_effect.name = "log"
        self.mock_effect.saved_state.return_value = None

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
        # Game opens on DP-1
        self.daemon.window_opened("win-game", "steam_app_1671210", "200", "DP-1", "true", "true")
        self.assertEqual(self.daemon.active_output, "DP-1")
        self.mock_effect.apply.assert_called_once_with("DP-1", ["DP-2", "DP-3"], "1671210")

        # Game closes
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
