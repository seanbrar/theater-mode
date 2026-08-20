"""Unit tests for daemon CLI and client CLI subcommands."""

from __future__ import annotations

import contextlib
import io
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from theater_mode.cli import (
    main as daemon_main,
)
from theater_mode.cli import (
    parse_args as parse_daemon_args,
)
from theater_mode.client import (
    _MISSING,
    _display_value,
    _format_diagnostics,
    _format_outputs,
    _format_provenance_table,
    _lookup,
    _parse_cli_value,
)
from theater_mode.client import (
    main as client_main,
)
from theater_mode.effects.base import EffectOptions
from theater_mode.effects.dim import DimEffect


class TestCLI(unittest.TestCase):
    def test_daemon_main_exits_nonzero_on_name_lost(self) -> None:
        with (
            patch("gi.repository.Gio.bus_own_name") as mock_bus_own,
            patch("gi.repository.GLib.MainLoop.run"),
            patch("theater_mode.cli.logging.basicConfig"),
            patch("theater_mode.cli.log.error"),
        ):

            def trigger_lost(*args, **kwargs):
                on_name_lost = mock_bus_own.call_args[0][5]
                on_name_lost()

            mock_bus_own.side_effect = trigger_lost
            result = daemon_main([])
            self.assertEqual(result, 1)

    def _run_client(self, argv: list[str], response: str) -> tuple[int, str, str, MagicMock]:
        call_dbus = MagicMock(return_value=response)
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = client_main(argv, call_dbus=call_dbus)
        return result, stdout.getvalue(), stderr.getvalue(), call_dbus

    def test_parse_daemon_defaults(self) -> None:
        args = parse_daemon_args([])
        self.assertFalse(args.verbose)
        self.assertIsNone(args.user_config_override)
        self.assertIsNone(args.system_config_override)

    def test_parse_daemon_dev_flags(self) -> None:
        args = parse_daemon_args(
            [
                "--verbose",
                "--replace-user-config",
                "/tmp/test_user.toml",
                "--replace-system-config",
                "/tmp/test_sys.toml",
            ]
        )
        self.assertTrue(args.verbose)
        self.assertEqual(args.user_config_override, Path("/tmp/test_user.toml"))
        self.assertEqual(args.system_config_override, Path("/tmp/test_sys.toml"))

    def test_effects_are_built_from_options(self) -> None:
        options = EffectOptions(dim_factor=0.5, dim_duration=9.0, dim_curve="quad", art=False)

        effect = DimEffect.create(options)
        self.assertEqual(effect._dim_factor, 0.5)
        self.assertEqual(effect._duration, 9.0)
        self.assertEqual(effect._curve, "quad")
        self.assertFalse(effect._art)

    def test_client_parse_cli_value(self) -> None:
        self.assertEqual(_parse_cli_value("true"), True)
        self.assertEqual(_parse_cli_value("false"), False)
        self.assertEqual(_parse_cli_value("0.85"), 0.85)
        self.assertEqual(_parse_cli_value("42"), 42)
        self.assertEqual(_parse_cli_value("dim"), "dim")

    def test_client_formatting(self) -> None:
        config_data = {
            "effect": {"mode": "dim", "dim_factor": 0.85, "art": True},
            "transition": {"duration": 2.0, "curve": "sine"},
            "daemon": {"revert_delay": 3.0, "stage_delay": 1.5, "require_fullscreen": False},
            "outputs": {"DP-1": {"dim_factor": 0.5}},
            "provenance": {
                "effect.dim_factor": {
                    "layer": "user",
                    "file": "/home/user/.config/config.toml",
                    "line": 4,
                },
                "outputs.DP-1.dim_factor": {"layer": "session", "file": None, "line": None},
            },
        }
        table = _format_provenance_table(config_data)
        self.assertIn("effect.dim_factor", table)
        self.assertIn("outputs.DP-1.dim_factor", table)
        self.assertIn("user", table)
        self.assertIn("session", table)

        diags = [
            {
                "key_path": "effect.dim_factor",
                "message": "Value 1.5 exceeds maximum 1.0",
                "severity": "error",
                "file": "/tmp/test.toml",
                "line": 3,
                "offending_value": 1.5,
                "substituted_value": 0.85,
            }
        ]
        diag_output = _format_diagnostics(diags)
        self.assertIn("Value 1.5 exceeds maximum 1.0", diag_output)
        self.assertIn("/tmp/test.toml:3", diag_output)

    def test_booleans_render_in_toml_spelling(self) -> None:
        """Verify boolean formatting matches TOML lowercase spelling."""
        self.assertEqual(_display_value(True), "true")
        self.assertEqual(_display_value(False), "false")
        # Everything else keeps its plain representation.
        self.assertEqual(_display_value("dim"), "dim")
        self.assertEqual(_display_value(0.85), "0.85")
        self.assertEqual(_display_value(3), "3")

    def test_provenance_table_shows_toml_booleans(self) -> None:
        table = _format_provenance_table(
            {
                "effect": {"art": True},
                "daemon": {"require_fullscreen": False},
                "outputs": {"DP-1": {"art": False}},
                "provenance": {},
            }
        )
        self.assertNotIn("True", table)
        self.assertNotIn("False", table)
        self.assertIn("true", table)
        self.assertIn("false", table)

    def test_config_get_prints_toml_booleans(self) -> None:
        raw = '{"effect": {"art": true}, "daemon": {"require_fullscreen": false}}'
        result, stdout, _, _ = self._run_client(["config", "get", "effect.art"], raw)
        self.assertEqual(result, 0)
        self.assertEqual(stdout.strip(), "true")

        result, stdout, _, _ = self._run_client(["config", "get", "daemon.require_fullscreen"], raw)
        self.assertEqual(result, 0)
        self.assertEqual(stdout.strip(), "false")

    def test_config_get_output_round_trips_through_config_set(self) -> None:
        """Verify config get output round-trips through config set."""
        for value in (True, False):
            self.assertIs(_parse_cli_value(_display_value(value)), value)

    def test_client_lookup_handles_output_ids_containing_dots(self) -> None:
        edid = "Dell Inc.:DELL S2721QS:4QCPZY3"
        data = {
            "effect": {"mode": "dim", "dim_factor": 0.85},
            "outputs": {edid: {"dim_factor": 0.3}},
        }

        self.assertEqual(_lookup(data, "effect.dim_factor"), 0.85)
        self.assertEqual(_lookup(data, f"outputs.{edid}.dim_factor"), 0.3)
        self.assertEqual(_lookup(data, f'outputs."{edid}".dim_factor'), 0.3)
        self.assertEqual(_lookup(data, "effect"), data["effect"])
        self.assertIs(_lookup(data, "effect.nope"), _MISSING)
        self.assertIs(_lookup(data, "outputs.DP-9.dim_factor"), _MISSING)

    def test_client_formats_output_identities(self) -> None:
        listing = _format_outputs(
            [
                {
                    "connector": "DP-2",
                    "vendor": "Dell Inc.",
                    "pnp_id": "DEL",
                    "model": "DELL S2721QS",
                    "serial": "4QCPZY3",
                    "match_keys": ["Dell Inc.:DELL S2721QS:4QCPZY3", "Dell Inc.:DELL S2721QS"],
                    "active": True,
                },
                {
                    "connector": "HDMI-A-1",
                    "vendor": None,
                    "pnp_id": None,
                    "model": None,
                    "serial": None,
                    "match_keys": [],
                    "active": False,
                },
            ]
        )

        self.assertIn('[outputs."Dell Inc.:DELL S2721QS:4QCPZY3"]', listing)
        self.assertIn("[outputs.DP-2]", listing)
        self.assertIn("(game display)", listing)
        # An output without EDID is still listed and addressable by connector.
        self.assertIn("no EDID reported", listing)
        self.assertIn("[outputs.HDMI-A-1]", listing)

    def test_client_formats_empty_output_list(self) -> None:
        self.assertIn("No connected outputs", _format_outputs([]))

    def test_client_main_simple_commands(self) -> None:
        cases = [
            (["status"], "Status", (), "Daemon active: 1 window(s)"),
            (["simulate", "1671210", "DP-1"], "Simulate", ("1671210", "DP-1"), "simulated"),
            (["clear"], "Clear", (), "cleared"),
            (["config", "revert-preview"], "RevertPreview", (), "preview reverted"),
            (["config", "reload"], "Reload", (), "reloaded"),
        ]
        for argv, method, args, response in cases:
            with self.subTest(argv=argv):
                result, stdout, _, call_dbus = self._run_client(argv, response)
                self.assertEqual(result, 0)
                self.assertEqual(stdout.strip(), response)
                call_dbus.assert_called_once_with(method, *args)

    def test_client_main_outputs(self) -> None:
        raw_json = '[{"connector": "DP-1", "active": true, "match_keys": ["Dell"]}]'
        result, stdout, _, call_dbus = self._run_client(["outputs"], raw_json)
        self.assertEqual(result, 0)
        self.assertIn("DP-1", stdout)
        call_dbus.assert_called_once_with("GetOutputs")

        result, stdout, _, _ = self._run_client(["outputs", "--json"], raw_json)
        self.assertEqual(result, 0)
        self.assertEqual(stdout.strip(), raw_json)

    def test_client_main_config_show_and_diagnostics(self) -> None:
        raw_config = (
            '{"effect": {"mode": "dim", "dim_factor": 0.85}, "transition": {}, "daemon": {}}'
        )
        result, stdout, _, _ = self._run_client(["config", "show"], raw_config)
        self.assertEqual(result, 0)
        self.assertIn("Resolved Configuration", stdout)

        result, stdout, _, _ = self._run_client(["config", "show", "--json"], raw_config)
        self.assertEqual(result, 0)
        self.assertEqual(stdout.strip(), raw_config)

        result, stdout, _, _ = self._run_client(["config", "diagnostics"], "[]")
        self.assertEqual(result, 0)
        self.assertIn("No configuration diagnostics", stdout)

    def test_client_main_config_get(self) -> None:
        raw_config = '{"effect": {"dim_factor": 0.85, "mode": "dim"}}'
        result, stdout, _, _ = self._run_client(["config", "get", "effect.dim_factor"], raw_config)
        self.assertEqual(result, 0)
        self.assertEqual(stdout.strip(), "0.85")

        result, _, stderr, _ = self._run_client(["config", "get", "nonexistent.key"], raw_config)
        self.assertEqual(result, 1)
        self.assertIn("error: key 'nonexistent.key' not found", stderr)

    def test_client_main_config_set_and_preview(self) -> None:
        result, _, _, call_dbus = self._run_client(
            ["config", "set", "effect.dim_factor", "0.5"], "committed 1 keys"
        )
        self.assertEqual(result, 0)
        call_dbus.assert_called_once_with("Commit", '{"effect.dim_factor": 0.5}')

        result, _, _, _ = self._run_client(
            ["config", "set", "effect.dim_factor", "99"],
            "error: nothing to commit (rejected: value 99 exceeds maximum)",
        )
        self.assertEqual(result, 1)

        result, _, _, call_dbus = self._run_client(
            ["config", "preview", "effect.dim_factor", "0.3"], "preview applied"
        )
        self.assertEqual(result, 0)
        call_dbus.assert_called_once_with("Preview", '{"effect.dim_factor": 0.3}')

    def test_client_main_version(self) -> None:
        stdout = io.StringIO()
        with (
            self.assertRaises(SystemExit) as caught,
            contextlib.redirect_stdout(stdout),
        ):
            client_main(["--version"])
        self.assertEqual(caught.exception.code, 0)
        self.assertIn("theater-mode", stdout.getvalue())

    def test_client_main_uninstall_missing_installer(self) -> None:
        with patch("pathlib.Path.is_file", return_value=False):
            result, _, stderr, _ = self._run_client(["uninstall"], "")
            self.assertEqual(result, 1)
            self.assertIn("no uninstaller found", stderr)

    def test_client_main_uninstall_executes_installer(self) -> None:
        with (
            patch("pathlib.Path.is_file", return_value=True),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value.returncode = 0
            result, _, _, _ = self._run_client(["uninstall", "-y"], "")
            self.assertEqual(result, 0)
            mock_run.assert_called_once()
            args = mock_run.call_args[0][0]
            self.assertIn("--uninstall", args)
            self.assertIn("--yes", args)


if __name__ == "__main__":
    unittest.main()
