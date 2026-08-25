"""Tests for the configuration showcase developer tool."""

from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock, patch
from unittest.mock import call as call_of

ROOT = Path(__file__).resolve().parent.parent
MODULE_PATH = ROOT / "tools" / "showcase.py"


def load_showcase() -> ModuleType:
    """Load the developer tool as a Python module."""
    spec = importlib.util.spec_from_file_location("showcase", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


showcase = load_showcase()


class ShowcaseTests(unittest.TestCase):
    """Exercise offline discovery, argument validation, and suite construction."""

    def test_list_does_not_contact_daemon(self) -> None:
        output = io.StringIO()
        with (
            patch.object(sys, "argv", [str(MODULE_PATH), "--list"]),
            patch.object(showcase, "daemon_status") as status,
            patch.object(showcase, "active_outputs") as outputs,
            redirect_stdout(output),
        ):
            self.assertEqual(showcase.main(), 0)

        status.assert_not_called()
        outputs.assert_not_called()
        self.assertIn("artwork", output.getvalue())
        self.assertIn("overrides", output.getvalue())

    def test_dry_run_accepts_offline_outputs_without_artwork(self) -> None:
        output = io.StringIO()
        argv = [
            str(MODULE_PATH),
            "--dry-run",
            "--suite",
            "outputs",
            "--output",
            "DP-1",
            "--output",
            "DP-2",
            "--interval",
            "0.001",
        ]
        with (
            patch.object(sys, "argv", argv),
            patch.object(showcase, "daemon_status") as status,
            patch.object(showcase, "active_outputs") as active,
            patch.object(showcase, "detect_appid") as detect_appid,
            patch.object(showcase, "call") as call,
            redirect_stdout(output),
        ):
            self.assertEqual(showcase.main(), 0)

        status.assert_not_called()
        active.assert_not_called()
        detect_appid.assert_not_called()
        call.assert_not_called()
        self.assertIn("[2/2] Game on DP-2", output.getvalue())

    def test_force_art_directory_participates_in_appid_detection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary)
            artwork = cache / "570" / "nested" / "library_hero.jpg"
            artwork.parent.mkdir(parents=True)
            artwork.touch()
            with patch.dict(os.environ, {"THEATER_DEV_FORCE_ART_DIR": str(cache)}):
                self.assertEqual(showcase.detect_appid(), "570")

    def test_detect_appid_returns_none_when_empty_or_non_numeric(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary)
            artwork = cache / "invalid_name" / "library_hero.jpg"
            artwork.parent.mkdir(parents=True)
            artwork.touch()
            with patch.dict(os.environ, {"THEATER_DEV_FORCE_ART_DIR": str(cache)}):
                self.assertIsNone(showcase.detect_appid())

    def test_overrides_adapts_to_available_secondary_outputs(self) -> None:
        dual = showcase.build_suites(["DP-1", "DP-2"], "DP-1")
        triple = showcase.build_suites(["DP-1", "DP-2", "DP-3"], "DP-1")

        self.assertIn("overrides", dual)
        self.assertEqual(len(dual["overrides"]), 1)
        self.assertIn("outputs.DP-2.art", dual["overrides"][0].updates)
        self.assertNotIn("outputs.DP-3.art", dual["overrides"][0].updates)

        self.assertIn("overrides", triple)
        self.assertEqual(len(triple["overrides"]), 1)
        self.assertIn("outputs.DP-2.art", triple["overrides"][0].updates)
        self.assertIn("outputs.DP-3.art", triple["overrides"][0].updates)

    def test_dry_run_runs_overrides_on_dual_displays(self) -> None:
        output = io.StringIO()
        argv = [
            str(MODULE_PATH),
            "--dry-run",
            "--suite",
            "overrides",
            "--output",
            "DP-1",
            "--output",
            "DP-2",
            "--interval",
            "0.001",
        ]
        with (
            patch.object(sys, "argv", argv),
            patch.object(showcase, "daemon_status") as status,
            patch.object(showcase, "active_outputs") as active,
            patch.object(showcase, "detect_appid") as detect_appid,
            patch.object(showcase, "call") as call,
            redirect_stdout(output),
        ):
            self.assertEqual(showcase.main(), 0)

        status.assert_not_called()
        active.assert_not_called()
        detect_appid.assert_not_called()
        call.assert_not_called()
        self.assertIn("Per-output override on DP-2", output.getvalue())
        self.assertIn("outputs.DP-2.art = true", output.getvalue())

    def test_end_of_input_stops_the_walk(self) -> None:
        output = io.StringIO()
        argv = [
            str(MODULE_PATH),
            "--dry-run",
            "--suite",
            "outputs",
            "--output",
            "DP-1",
            "--output",
            "DP-2",
        ]
        with (
            patch.object(sys, "argv", argv),
            patch("builtins.input", side_effect=EOFError),
            redirect_stdout(output),
        ):
            self.assertEqual(showcase.main(), 0)

        self.assertIn("[1/2]", output.getvalue())
        self.assertNotIn("[2/2]", output.getvalue())

    def test_newlines_on_stdin_walk_that_many_cases(self) -> None:
        output = io.StringIO()
        argv = [
            str(MODULE_PATH),
            "--dry-run",
            "--suite",
            "flat",
            "--output",
            "DP-1",
            "--output",
            "DP-2",
        ]
        with (
            patch.object(sys, "argv", argv),
            patch("builtins.input", side_effect=["", "", EOFError()]),
            redirect_stdout(output),
        ):
            self.assertEqual(showcase.main(), 0)

        self.assertIn("[3/5]", output.getvalue())
        self.assertNotIn("[4/5]", output.getvalue())

    def test_interrupt_restores_the_daemon_and_reports_status(self) -> None:
        steps = showcase.build_suites(["DP-1", "DP-2"], "DP-1")["flat"]
        with (
            patch.object(showcase, "call") as call,
            patch.object(showcase.time, "sleep", side_effect=KeyboardInterrupt),
            redirect_stdout(io.StringIO()),
        ):
            status = showcase.run(steps, "440", 5.0, dry_run=False)

        self.assertEqual(status, 130)
        self.assertEqual(call.call_args_list[-2:], [call_of("Clear"), call_of("RevertPreview")])

    def test_cleanup_tolerates_daemon_disconnect(self) -> None:
        steps = showcase.build_suites(["DP-1", "DP-2"], "DP-1")["flat"]
        errors = io.StringIO()
        with (
            patch.object(
                showcase,
                "call",
                side_effect=[
                    None,
                    None,
                    RuntimeError("disconnected"),
                    RuntimeError("disconnected"),
                ],
            ),
            redirect_stdout(io.StringIO()),
            redirect_stderr(errors),
        ):
            status = showcase.run(steps[:1], "440", 0.001, dry_run=False)

        self.assertEqual(status, 1)
        self.assertIn("could not clear", errors.getvalue())
        self.assertIn("could not discard", errors.getvalue())

    def test_call_rejects_errors(self) -> None:
        with patch.object(showcase, "_call_dbus_method", return_value="error: rejected"):
            with self.assertRaisesRegex(RuntimeError, "error: rejected"):
                showcase.call("Status")

    def test_call_converts_client_exit_to_runtime_error(self) -> None:
        with patch.object(showcase, "_call_dbus_method", side_effect=SystemExit(1)):
            with self.assertRaisesRegex(RuntimeError, "connection failed"):
                showcase.call("Status")

    def test_daemon_status_parses_json(self) -> None:
        data = json.dumps({"outputs": ["DP-1", "HDMI-1"], "games": []})
        with patch.object(showcase, "call", return_value=data):
            status = showcase.daemon_status()
        self.assertEqual(status["outputs"], ["DP-1", "HDMI-1"])

    def test_daemon_status_rejects_malformed_response(self) -> None:
        for data in ("not json", "[]"):
            with self.subTest(data=data), patch.object(showcase, "call", return_value=data):
                with self.assertRaisesRegex(RuntimeError, "invalid Status response"):
                    showcase.daemon_status()

    def test_active_outputs_excludes_disabled_and_disconnected_connectors(self) -> None:
        configuration = {
            "outputs": [
                {"name": "DP-1", "connected": True, "enabled": True},
                {"name": "DP-2", "connected": True, "enabled": False},
                {"name": "DP-3", "connected": False, "enabled": True},
                {"name": "HDMI-A-1", "connected": True, "enabled": True},
            ]
        }
        completed = Mock(stdout=json.dumps(configuration), stderr="", returncode=0)
        with patch.object(showcase.subprocess, "run", return_value=completed) as run:
            self.assertEqual(showcase.active_outputs(), ["DP-1", "HDMI-A-1"])

        run.assert_called_once_with(
            ["kscreen-doctor", "--json"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )

    def test_active_outputs_rejects_malformed_topology(self) -> None:
        for configuration in (
            {},
            {"outputs": [4]},
            {"outputs": [{"connected": True, "enabled": True}]},
            {"outputs": [{"connected": "yes", "enabled": True, "name": "DP-1"}]},
        ):
            with self.subTest(configuration=configuration):
                completed = Mock(stdout=json.dumps(configuration), stderr="", returncode=0)
                with patch.object(showcase.subprocess, "run", return_value=completed):
                    with self.assertRaisesRegex(RuntimeError, "invalid kscreen-doctor response"):
                        showcase.active_outputs()

    def test_active_outputs_bounds_kscreen_query(self) -> None:
        expired = subprocess.TimeoutExpired(["kscreen-doctor", "--json"], 5)
        with patch.object(showcase.subprocess, "run", side_effect=expired):
            with self.assertRaisesRegex(RuntimeError, "did not respond within 5 seconds"):
                showcase.active_outputs()

    def test_session_preview_keys_reads_provenance(self) -> None:
        resolved = {
            "provenance": {
                "effect.art": {"layer": "builtin"},
                "effect.dimming": {"layer": "session"},
                "outputs.DP-2.art": {"layer": "session"},
            }
        }
        with patch.object(showcase, "call", return_value=json.dumps(resolved)):
            self.assertEqual(
                showcase.session_preview_keys(),
                ["effect.dimming", "outputs.DP-2.art"],
            )

    def test_main_refuses_to_replace_an_existing_session_preview(self) -> None:
        argv = [str(MODULE_PATH), "--suite", "flat", "--appid", "440"]
        status = {"outputs": ["DP-1", "DP-2"], "games": []}
        resolved = {"provenance": {"effect.dimming": {"layer": "session"}}}
        errors = io.StringIO()
        with (
            patch.object(sys, "argv", argv),
            patch.object(showcase, "active_outputs", return_value=["DP-1", "DP-2"]),
            patch.object(
                showcase,
                "call",
                side_effect=[json.dumps(status), json.dumps(resolved)],
            ) as call,
            redirect_stderr(errors),
            self.assertRaises(SystemExit) as raised,
        ):
            showcase.main()

        self.assertEqual(raised.exception.code, 2)
        self.assertEqual(call.call_args_list, [call_of("Status"), call_of("GetResolved")])
        self.assertIn("session preview settings are already active", errors.getvalue())

    def test_invalid_interval_is_rejected_before_daemon_access(self) -> None:
        errors = io.StringIO()
        with (
            patch.object(sys, "argv", [str(MODULE_PATH), "--interval", "0"]),
            patch.object(showcase, "daemon_status") as status,
            patch.object(showcase, "active_outputs") as active,
            redirect_stderr(errors),
            self.assertRaises(SystemExit) as raised,
        ):
            showcase.main()

        self.assertEqual(raised.exception.code, 2)
        status.assert_not_called()
        active.assert_not_called()
        self.assertIn("greater than zero", errors.getvalue())

    def test_main_rejects_invalid_arguments(self) -> None:
        cases = [
            ([str(MODULE_PATH), "--appid", "abc", "--dry-run"], "positive integer"),
            ([str(MODULE_PATH), "--appid", "0", "--dry-run"], "positive integer"),
            ([str(MODULE_PATH), "--output", "DP-1"], "only valid with --dry-run"),
            (
                [str(MODULE_PATH), "--dry-run", "--output", "DP-1", "--output", "DP-1"],
                "must be unique",
            ),
            ([str(MODULE_PATH), "--dry-run", "--output", "DP-1"], "at least two"),
            (
                [str(MODULE_PATH), "--dry-run", "--output", "", "--output", "DP-2"],
                "must not be empty",
            ),
            (
                [
                    str(MODULE_PATH),
                    "--dry-run",
                    "--output",
                    "DP-1",
                    "--output",
                    "DP-2",
                    "--game-output",
                    "DP-9",
                ],
                "unknown game output",
            ),
            (
                [
                    str(MODULE_PATH),
                    "--dry-run",
                    "--output",
                    "DP-1",
                    "--output",
                    "DP-2",
                    "--suite",
                    "nonexistent",
                ],
                "unknown suite",
            ),
        ]
        for argv, expected_msg in cases:
            with self.subTest(argv=argv), patch.object(sys, "argv", argv):
                errors = io.StringIO()
                with redirect_stderr(errors), self.assertRaises(SystemExit):
                    showcase.main()
                self.assertIn(expected_msg, errors.getvalue())

    def test_advance_parses_commands(self) -> None:
        commands = [
            ("", "next"),
            ("   ", "next"),
            ("n", "next"),
            ("p", "prev"),
            ("prev", "prev"),
            ("back", "prev"),
            ("r", "replay"),
            ("replay", "replay"),
            ("q", "quit"),
            ("quit", "quit"),
        ]
        for user_input, expected in commands:
            with (
                self.subTest(user_input=user_input),
                patch("builtins.input", return_value=user_input),
            ):
                self.assertEqual(showcase.advance("prompt: "), expected)

        with patch("builtins.input", side_effect=EOFError):
            self.assertEqual(showcase.advance("prompt: "), "quit")

    def test_game_title_for_appid_reads_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            steam_root = Path(tmp) / "Steam"
            steamapps = steam_root / "steamapps"
            steamapps.mkdir(parents=True)
            manifest = steamapps / "appmanifest_440.acf"
            manifest.write_text(
                '"AppState"\n{\n\t"appid"\t\t"440"\n\t"name"\t\t"Team Fortress 2"\n}\n'
            )

            cache = steam_root / "appcache" / "librarycache"
            with patch.object(showcase, "STEAM_LIBRARY_CACHES", (cache,)):
                self.assertEqual(showcase.game_title_for_appid("440"), "Team Fortress 2")
                self.assertIsNone(showcase.game_title_for_appid("99999"))

    def test_run_interactive_navigation(self) -> None:
        steps = showcase.build_suites(["DP-1", "DP-2"], "DP-1")["compare"]
        user_inputs = ["", "p", "r", "q"]
        output = io.StringIO()
        with (
            patch("builtins.input", side_effect=user_inputs),
            patch.object(showcase, "STEAM_LIBRARY_CACHES", ()),
            redirect_stdout(output),
        ):
            status = showcase.run(steps, "440", None, dry_run=True)
        self.assertEqual(status, 0)
        rendered = output.getvalue()
        # Enter advanced to 2, p went back to 1, r re-showed 1, q stopped there.
        self.assertEqual(rendered.count("[1/4]"), 3)
        self.assertEqual(rendered.count("[2/4]"), 1)
        self.assertNotIn("[3/4]", rendered)

    def test_replay_clears_before_reapplying(self) -> None:
        """Without the Clear, a replay re-sends identical values and nothing moves."""
        steps = showcase.build_suites(["DP-1", "DP-2"], "DP-1")["compare"]
        with (
            patch.object(showcase, "call") as call,
            patch.object(showcase.time, "sleep"),
            patch.object(showcase, "STEAM_LIBRARY_CACHES", ()),
            patch("builtins.input", side_effect=["r", "q"]),
            redirect_stdout(io.StringIO()),
        ):
            status = showcase.run(steps, "440", None, dry_run=False)

        self.assertEqual(status, 0)
        self.assertEqual(
            [c.args[0] for c in call.call_args_list],
            ["Preview", "Simulate", "Clear", "Preview", "Simulate", "Clear", "RevertPreview"],
        )


if __name__ == "__main__":
    unittest.main()
