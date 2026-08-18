"""Unit tests for daemon CLI and client CLI subcommands."""

from __future__ import annotations

import unittest
from pathlib import Path

from theater_mode.cli import parse_args as parse_daemon_args
from theater_mode.client import (
    _MISSING,
    _format_diagnostics,
    _format_outputs,
    _format_provenance_table,
    _lookup,
    _parse_cli_value,
)
from theater_mode.effects import EFFECTS
from theater_mode.effects.base import EffectOptions
from theater_mode.effects.dim import DimEffect
from theater_mode.effects.log import LogEffect


class TestCLI(unittest.TestCase):
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

        effect = EFFECTS["dim"].create(options)
        self.assertIsInstance(effect, DimEffect)
        self.assertEqual(effect._dim_factor, 0.5)
        self.assertEqual(effect._duration, 9.0)
        self.assertEqual(effect._curve, "quad")
        self.assertFalse(effect._art)

        # Effects without settings ignore the options entirely.
        self.assertIsInstance(EFFECTS["log"].create(options), LogEffect)

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


if __name__ == "__main__":
    unittest.main()
