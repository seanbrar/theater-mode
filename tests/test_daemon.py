"""Unit tests for Daemon state machine and window tracking."""

from __future__ import annotations

import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from theater_mode.config import DaemonConfig, DevConfig, ResolvedConfig
from theater_mode.daemon import Daemon


class FakeScheduler:
    """Deterministic scheduler test double for time advancement and assertions."""

    def __init__(self) -> None:
        self.current_time_ms: int = 0
        self._next_id: int = 1
        self.timers: dict[int, tuple[int, Callable[[], None]]] = {}

    def timeout_add(self, delay_ms: int, callback: Callable[[], None]) -> int:
        tag = self._next_id
        self._next_id += 1
        self.timers[tag] = (self.current_time_ms + delay_ms, callback)
        return tag

    def source_remove(self, tag: Any) -> None:
        self.timers.pop(tag, None)

    def advance(self, ms: int) -> None:
        target_time = self.current_time_ms + ms
        while True:
            ready = [(tag, due, cb) for tag, (due, cb) in self.timers.items() if due <= target_time]
            if not ready:
                break
            ready.sort(key=lambda item: item[1])
            tag, due, cb = ready[0]
            self.current_time_ms = due
            self.timers.pop(tag, None)
            cb()
        self.current_time_ms = target_time

    def advance_sec(self, seconds: float) -> None:
        self.advance(int(seconds * 1000))

    @property
    def pending_count(self) -> int:
        return len(self.timers)


class TestDaemon(unittest.TestCase):
    def setUp(self) -> None:
        self.mock_effect = MagicMock()
        self.mock_effect.name = "dim"

        # Point both config layers at an empty temp dir so reloads never read the
        # developer's own ~/.config/theater-mode/config.toml.
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        config_dir = Path(self.temp_dir.name)

        self.daemon = Daemon(
            effect=self.mock_effect,
            config=ResolvedConfig(daemon=DaemonConfig(revert_delay=0.0, stage_delay=0.0)),
            dev_config=DevConfig(
                user_config_override=config_dir / "user.toml",
                system_config_override=config_dir / "system.toml",
            ),
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

    def test_config_dbus_methods(self) -> None:
        # GetResolved
        resolved_json = self.daemon.get_resolved()
        self.assertIn("effect", resolved_json)
        self.assertIn("provenance", resolved_json)

        # GetDiagnostics
        diags_json = self.daemon.get_diagnostics()
        self.assertEqual(diags_json, "[]")

        # Preview
        prev_res = self.daemon.preview('{"effect.dim_factor": 0.33}')
        self.assertIn("preview applied", prev_res)
        self.assertEqual(self.daemon.config.effect.dim_factor, 0.33)

        # RevertPreview
        revert_res = self.daemon.revert_preview()
        self.assertIn("preview reverted", revert_res)
        self.assertEqual(self.daemon.config.effect.dim_factor, 0.85)

        # Reload falls back to built-in defaults (both layers are empty)
        reload_res = self.daemon.reload()
        self.assertIn("reloaded", reload_res)
        self.assertEqual(self.daemon.config.daemon.revert_delay, 3.0)

    def test_invalid_keys_are_rejected_not_persisted(self) -> None:
        result = self.daemon.commit('{"effect.dim_factor": 99.0, "effect.nonsense": 1}')
        self.assertTrue(result.startswith("error: nothing to commit"))
        self.assertIn("exceeds maximum", result)
        self.assertIn("Unknown configuration key", result)
        self.assertFalse((self.daemon.dev_config.user_config_override).exists())

    def test_commit_persists_valid_keys(self) -> None:
        result = self.daemon.commit('{"effect.dim_factor": 0.4}')
        self.assertIn("committed 1 keys", result)
        self.assertEqual(self.daemon.config.effect.dim_factor, 0.4)

    @patch("theater_mode.daemon.connected_outputs", return_value={"DP-1", "DP-2"})
    def test_revert_delay_fades_out_after_timeout(self, _) -> None:
        scheduler = FakeScheduler()
        daemon = Daemon(
            effect=self.mock_effect,
            config=ResolvedConfig(daemon=DaemonConfig(revert_delay=3.0, stage_delay=0.0)),
            scheduler=scheduler,
        )

        daemon.window_opened("win-game", "steam_app_100", "200", "DP-1", "true")
        self.mock_effect.apply.assert_called_once_with("DP-1", ["DP-2"], "100")
        self.assertEqual(daemon.active_output, "DP-1")

        # Closing the game initiates revert delay timer instead of reverting immediately
        daemon.window_closed("win-game")
        self.mock_effect.revert.assert_not_called()
        self.assertEqual(scheduler.pending_count, 1)

        # Before timeout expires (2.0s), effect remains active
        scheduler.advance_sec(2.0)
        self.mock_effect.revert.assert_not_called()
        self.assertEqual(daemon.active_output, "DP-1")

        # Passing the 3.0s threshold triggers effect reversion
        scheduler.advance_sec(1.1)
        self.mock_effect.revert.assert_called_once()
        self.assertIsNone(daemon.active_output)
        self.assertEqual(scheduler.pending_count, 0)

    @patch("theater_mode.daemon.connected_outputs", return_value={"DP-1", "DP-2"})
    def test_revert_delay_cancelled_by_new_game_window(self, _) -> None:
        scheduler = FakeScheduler()
        daemon = Daemon(
            effect=self.mock_effect,
            config=ResolvedConfig(daemon=DaemonConfig(revert_delay=3.0, stage_delay=0.0)),
            scheduler=scheduler,
        )

        daemon.window_opened("win-1", "steam_app_100", "200", "DP-1", "true")
        daemon.window_closed("win-1")
        self.assertEqual(scheduler.pending_count, 1)

        # 1.5s into the 3.0s revert delay, a new game window opens
        scheduler.advance_sec(1.5)
        daemon.window_opened("win-2", "steam_app_200", "201", "DP-1", "true")

        # Pending revert should be cancelled
        self.assertEqual(scheduler.pending_count, 0)

        # Advance past the original 3.0s timeout: revert is never called
        scheduler.advance_sec(5.0)
        self.mock_effect.revert.assert_not_called()
        self.assertEqual(daemon.active_output, "DP-1")

    @patch("theater_mode.daemon.connected_outputs", return_value={"DP-1", "DP-2"})
    def test_stage_delay_on_output_migration(self, _) -> None:
        scheduler = FakeScheduler()
        daemon = Daemon(
            effect=self.mock_effect,
            config=ResolvedConfig(daemon=DaemonConfig(revert_delay=0.0, stage_delay=1.5)),
            scheduler=scheduler,
        )

        # Start game on DP-1
        daemon.window_opened("win-1", "steam_app_100", "200", "DP-1", "true")
        self.mock_effect.apply.assert_called_once_with("DP-1", ["DP-2"], "100")
        self.assertEqual(daemon.active_output, "DP-1")

        # Game migrates to DP-2
        daemon.window_changed("win-1", "DP-2", "true")
        self.assertEqual(scheduler.pending_count, 1)

        # While stage timer is pending (1.0s), active output stays on DP-1
        scheduler.advance_sec(1.0)
        self.assertEqual(daemon.active_output, "DP-1")
        self.assertEqual(self.mock_effect.apply.call_count, 1)

        # After stage timer completes (1.5s total), migration commits to DP-2
        scheduler.advance_sec(0.6)
        self.mock_effect.revert.assert_called_once()
        self.assertEqual(self.mock_effect.apply.call_count, 2)
        self.assertEqual(self.mock_effect.apply.call_args.args, ("DP-2", ["DP-1"], "100"))
        self.assertEqual(daemon.active_output, "DP-2")

    @patch("theater_mode.daemon.connected_outputs", return_value={"DP-1", "DP-2"})
    def test_stage_delay_cancelled_if_window_returns_to_original_screen(self, _) -> None:
        scheduler = FakeScheduler()
        daemon = Daemon(
            effect=self.mock_effect,
            config=ResolvedConfig(daemon=DaemonConfig(revert_delay=0.0, stage_delay=1.5)),
            scheduler=scheduler,
        )

        daemon.window_opened("win-1", "steam_app_100", "200", "DP-1", "true")
        daemon.window_changed("win-1", "DP-2", "true")
        self.assertEqual(scheduler.pending_count, 1)

        # Window quickly flickers back to DP-1 before 1.5s timeout
        scheduler.advance_sec(0.5)
        daemon.window_changed("win-1", "DP-1", "true")
        self.assertEqual(scheduler.pending_count, 0)

        # Advance past 1.5s: effect never migrated away from DP-1
        scheduler.advance_sec(3.0)
        self.mock_effect.revert.assert_not_called()
        self.assertEqual(self.mock_effect.apply.call_count, 1)
        self.assertEqual(daemon.active_output, "DP-1")


if __name__ == "__main__":
    unittest.main()
