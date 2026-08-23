"""Unit tests for configuration schema, 3-layer resolution, provenance, and writer."""

from __future__ import annotations

import os
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

from theater_mode.config import (
    ConfigLoader,
    DevConfig,
    Layer,
    commit_user_config,
    generate_reference_config,
    get_default_system_path,
    load_resolved_config,
    system_config_dirs,
    unset_user_config,
    update_toml_content,
    validate_updates,
)


class TestConfig(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.temp_dir.name)
        self.sys_path = self.dir_path / "system.toml"
        self.user_path = self.dir_path / "user.toml"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_default_config_resolution(self) -> None:
        """Test default config resolution with no files present."""
        dev = DevConfig(
            user_config_override=self.user_path,
            system_config_override=self.sys_path,
        )
        config, diagnostics = load_resolved_config(dev_config=dev)

        self.assertEqual(len(diagnostics), 0)
        self.assertEqual(config.effect.placement, "over_windows")
        self.assertEqual(config.effect.dim_factor, 0.85)
        self.assertTrue(config.effect.art)
        self.assertEqual(config.transition.duration, 2.0)
        self.assertEqual(config.transition.curve, "sine")
        self.assertEqual(config.daemon.revert_delay, 3.0)

        for prov in config.provenance.values():
            self.assertEqual(prov.layer, Layer.BUILTIN)
            self.assertIsNone(prov.file_path)

    def test_three_layer_resolution_and_provenance(self) -> None:
        """Test 3-layer precedence: Default -> System -> User."""
        self.sys_path.write_text(
            """
[effect]
dim_factor = 0.50
art = false

[transition]
duration = 4.0
""",
            encoding="utf-8",
        )

        self.user_path.write_text(
            """
[effect]
dim_factor = 0.70
# custom user duration
[transition]
duration = 1.0
""",
            encoding="utf-8",
        )

        dev = DevConfig(
            user_config_override=self.user_path,
            system_config_override=self.sys_path,
        )
        config, diagnostics = load_resolved_config(dev_config=dev)

        self.assertEqual(len(diagnostics), 0)
        self.assertEqual(config.effect.dim_factor, 0.70)
        self.assertEqual(config.provenance["effect.dim_factor"].layer, Layer.USER)
        self.assertEqual(config.provenance["effect.dim_factor"].file_path, self.user_path)
        self.assertEqual(config.provenance["effect.dim_factor"].line_number, 3)

        self.assertEqual(config.transition.duration, 1.0)
        self.assertEqual(config.provenance["transition.duration"].layer, Layer.USER)

        self.assertFalse(config.effect.art)
        self.assertEqual(config.provenance["effect.art"].layer, Layer.SYSTEM)
        self.assertEqual(config.provenance["effect.art"].file_path, self.sys_path)
        self.assertEqual(config.provenance["effect.art"].line_number, 4)

        self.assertEqual(config.transition.curve, "sine")
        self.assertEqual(config.provenance["transition.curve"].layer, Layer.BUILTIN)
        self.assertEqual(config.daemon.revert_delay, 3.0)

    def test_per_output_overrides_and_hierarchy(self) -> None:
        """Test per-output overrides and match priority."""
        self.user_path.write_text(
            """
[effect]
dim_factor = 0.80
placement = "behind_windows"

[outputs.DP-1]
dim_factor = 0.40
placement = "over_windows"
art = false

[outputs."LG:27GL850:1234"]
dim_factor = 0.95
""",
            encoding="utf-8",
        )

        dev = DevConfig(
            user_config_override=self.user_path,
            system_config_override=self.sys_path,
        )
        config, diagnostics = load_resolved_config(dev_config=dev)
        self.assertEqual(len(diagnostics), 0)

        dp1 = config.resolve_for_output("DP-1")
        self.assertEqual(dp1.dim_factor, 0.40)
        self.assertEqual(dp1.placement, "over_windows")
        self.assertFalse(dp1.art)
        self.assertEqual(dp1.curve, "sine")

        lg = config.resolve_for_output("DP-1", ["LG:27GL850:1234", "LG:27GL850"])
        self.assertEqual(lg.dim_factor, 0.95)
        self.assertEqual(lg.placement, "behind_windows")
        self.assertTrue(lg.art)

        fallback = config.resolve_for_output("DP-1", ["Dell Inc.:U2720Q:ABC", "Dell Inc.:U2720Q"])
        self.assertEqual(fallback.dim_factor, 0.40)
        self.assertEqual(fallback.placement, "over_windows")

        self.assertEqual(dp1.matched_key, "DP-1")
        self.assertEqual(lg.matched_key, "LG:27GL850:1234")

        hdmi = config.resolve_for_output("HDMI-A-1")
        self.assertIsNone(hdmi.matched_key)
        self.assertEqual(hdmi.dim_factor, 0.80)
        self.assertEqual(hdmi.placement, "behind_windows")
        self.assertTrue(hdmi.art)

    def test_invalid_keys_in_output_produce_diagnostics(self) -> None:
        """Test that non-output keys in [outputs.<id>] are rejected with diagnostics."""
        self.user_path.write_text(
            """
[outputs.DP-1]
revert_delay = 5.0
dim_factor = 0.50
""",
            encoding="utf-8",
        )

        dev = DevConfig(
            user_config_override=self.user_path,
            system_config_override=self.sys_path,
        )
        config, diagnostics = load_resolved_config(dev_config=dev)

        self.assertEqual(len(diagnostics), 1)
        self.assertIn("not allowed in per-output table", diagnostics[0].message)
        self.assertEqual(diagnostics[0].key_path, "outputs.DP-1.revert_delay")
        self.assertEqual(config.resolve_for_output("DP-1").dim_factor, 0.50)

    def test_malformed_toml_failure_posture(self) -> None:
        """Test that unparseable TOML does not crash and collects a diagnostic."""
        self.user_path.write_text(
            """
[effect
dim_factor = "missing closing bracket
""",
            encoding="utf-8",
        )

        dev = DevConfig(
            user_config_override=self.user_path,
            system_config_override=self.sys_path,
        )
        config, diagnostics = load_resolved_config(dev_config=dev)

        self.assertGreaterEqual(len(diagnostics), 1)
        self.assertIn("Malformed TOML", diagnostics[0].message)
        self.assertEqual(config.effect.dim_factor, 0.85)

    def test_out_of_range_and_invalid_choices(self) -> None:
        """Test numeric clamping/rejection and invalid enum choices."""
        self.user_path.write_text(
            """
[effect]
dim_factor = 1.50
placement = "invalid_placement"

[transition]
curve = "hyperbolic"
duration = -5.0
""",
            encoding="utf-8",
        )

        dev = DevConfig(
            user_config_override=self.user_path,
            system_config_override=self.sys_path,
        )
        config, diagnostics = load_resolved_config(dev_config=dev)

        self.assertEqual(len(diagnostics), 4)
        self.assertEqual(config.effect.dim_factor, 0.85)
        self.assertEqual(config.effect.placement, "over_windows")
        self.assertEqual(config.transition.curve, "sine")
        self.assertEqual(config.transition.duration, 2.0)

    def test_session_preview_layer(self) -> None:
        """Test ephemeral session preview overrides."""
        dev = DevConfig(
            user_config_override=self.user_path,
            system_config_override=self.sys_path,
        )
        loader = ConfigLoader(
            dev_config=dev,
            session_overrides={
                "effect.dim_factor": 0.25,
                "outputs.DP-1.dim_factor": 0.10,
            },
        )
        config = loader.resolve()

        self.assertEqual(config.effect.dim_factor, 0.25)
        self.assertEqual(config.provenance["effect.dim_factor"].layer, Layer.SESSION)

        dp1 = config.resolve_for_output("DP-1")
        self.assertEqual(dp1.dim_factor, 0.10)
        self.assertEqual(config.provenance["outputs.DP-1.dim_factor"].layer, Layer.SESSION)

    def test_format_preserving_toml_writer(self) -> None:
        """Test atomic comment-preserving updates."""
        original = """# My awesome theater mode config
[effect]
placement = "over_windows"
dim_factor = 0.85  # Keep this comment

[daemon]
revert_delay = 3.0
"""
        updated = update_toml_content(
            original,
            {
                "effect.dim_factor": 0.65,
                "transition.duration": 1.5,
                "outputs.DP-1.dim_factor": 0.30,
            },
        )

        self.assertIn("# My awesome theater mode config", updated)
        self.assertIn("dim_factor = 0.65  # Keep this comment", updated)
        self.assertIn("[transition]", updated)
        self.assertIn("duration = 1.5", updated)
        self.assertIn("[outputs.DP-1]", updated)
        self.assertIn("dim_factor = 0.3", updated)

        parsed = tomllib.loads(updated)
        self.assertEqual(parsed["effect"]["dim_factor"], 0.65)
        self.assertEqual(parsed["transition"]["duration"], 1.5)
        self.assertEqual(parsed["outputs"]["DP-1"]["dim_factor"], 0.30)

    def test_commit_user_config_validates_toml_syntax(self) -> None:
        self.user_path.write_text("[effect]\ninvalid toml syntax = ", encoding="utf-8")
        ok, msg = commit_user_config({"effect.dim_factor": 0.5}, self.user_path)
        self.assertFalse(ok)
        self.assertIn("invalid TOML syntax", msg)
        self.assertEqual(
            self.user_path.read_text(encoding="utf-8"), "[effect]\ninvalid toml syntax = "
        )

    def test_commit_user_config_handles_directory_creation_failure(self) -> None:
        with patch("pathlib.Path.mkdir", side_effect=OSError("Permission denied")):
            ok, msg = commit_user_config({"effect.dim_factor": 0.5}, self.user_path)
            self.assertFalse(ok)
            self.assertIn("Failed to create directory", msg)

    def test_unset_user_config_validates_toml_syntax(self) -> None:
        self.user_path.write_text(
            "[effect]\ndim_factor = 0.5\ninvalid toml syntax = ", encoding="utf-8"
        )
        ok, msg, removed = unset_user_config({"effect.dim_factor"}, self.user_path)
        self.assertFalse(ok)
        self.assertIn("invalid TOML syntax", msg)
        self.assertEqual(removed, set())

    def test_commit_user_config_file(self) -> None:
        """Test writing user config to disk atomically."""
        ok, _ = commit_user_config(
            {"effect.placement": "behind_windows", "effect.dim_factor": 0.5},
            user_config_path=self.user_path,
        )
        self.assertTrue(ok)
        self.assertTrue(self.user_path.is_file())

        parsed = tomllib.loads(self.user_path.read_text(encoding="utf-8"))
        self.assertEqual(parsed["effect"]["placement"], "behind_windows")
        self.assertEqual(parsed["effect"]["dim_factor"], 0.5)

    def test_writer_quotes_non_bare_output_ids(self) -> None:
        """EDID-style output ids must be quoted, or the file stops parsing."""
        edid = "LG Electronics:27GL850:0x0001"
        ok, _ = commit_user_config({f"outputs.{edid}.dim_factor": 0.9}, self.user_path)
        self.assertTrue(ok)

        parsed = tomllib.loads(self.user_path.read_text(encoding="utf-8"))
        self.assertEqual(parsed["outputs"][edid]["dim_factor"], 0.9)

        commit_user_config({f"outputs.{edid}.dim_factor": 0.95}, self.user_path)
        parsed = tomllib.loads(self.user_path.read_text(encoding="utf-8"))
        self.assertEqual(parsed["outputs"][edid]["dim_factor"], 0.95)

    def test_output_ids_containing_dots_round_trip(self) -> None:
        """Vendor names like 'Dell Inc.' must not be split into nested tables."""
        edid = "Dell Inc.:DELL S2721QS:4QCPZY3"
        ok, _ = commit_user_config(
            {
                f"outputs.{edid}.dim_factor": 0.3,
                # Quoted and unquoted spellings address the same output ID.
                f'outputs."{edid}".art': False,
            },
            self.user_path,
        )
        self.assertTrue(ok)

        parsed = tomllib.loads(self.user_path.read_text(encoding="utf-8"))
        self.assertEqual(list(parsed["outputs"]), [edid])
        self.assertEqual(parsed["outputs"][edid], {"art": False, "dim_factor": 0.3})

        dev = DevConfig(user_config_override=self.user_path, system_config_override=self.sys_path)
        config, diagnostics = load_resolved_config(dev_config=dev)
        self.assertEqual(diagnostics, [])
        self.assertEqual(config.resolve_for_output("DP-2", [edid]).dim_factor, 0.3)
        self.assertEqual(config.provenance[f"outputs.{edid}.dim_factor"].line_number, 3)

    def test_writer_keeps_new_keys_inside_their_own_table(self) -> None:
        updated = update_toml_content(
            '[effect]\nplacement = "over_windows"\n\n# daemon settings\n[daemon]\nrevert_delay = 3.0\n',
            {"effect.dim_factor": 0.5},
        )
        self.assertEqual(
            updated.splitlines(),
            [
                "[effect]",
                'placement = "over_windows"',
                "dim_factor = 0.5",
                "",
                "# daemon settings",
                "[daemon]",
                "revert_delay = 3.0",
            ],
        )

    def test_provenance_lines_survive_quoted_output_tables(self) -> None:
        self.user_path.write_text(
            '[effect]\ndim_factor = 0.8\n\n[outputs."LG:27GL850:1234"]\ndim_factor = 0.95\n',
            encoding="utf-8",
        )
        dev = DevConfig(user_config_override=self.user_path, system_config_override=self.sys_path)
        config, _ = load_resolved_config(dev_config=dev)

        self.assertEqual(config.provenance["effect.dim_factor"].line_number, 2)
        self.assertEqual(config.provenance["outputs.LG:27GL850:1234.dim_factor"].line_number, 5)

    def test_validate_updates_rejects_unknown_and_out_of_range(self) -> None:
        accepted, rejected = validate_updates(
            {
                "effect.dim_factor": 0.5,
                "effect.dim_factor_typo": 0.5,
                "transition.duration": 999.0,
                "outputs.DP-1.revert_delay": 1.0,
                "effect.placement": "BEHIND_WINDOWS",
            }
        )
        self.assertEqual(accepted, {"effect.dim_factor": 0.5, "effect.placement": "behind_windows"})
        self.assertEqual(len(rejected), 3)

    def test_system_layer_searches_xdg_config_dirs_in_order(self) -> None:
        """Verify system config resolution searches XDG_CONFIG_DIRS in order."""
        first = self.dir_path / "kdedefaults"
        second = self.dir_path / "etc-xdg"
        for directory in (first, second):
            (directory / "theater-mode").mkdir(parents=True)
        first_file = first / "theater-mode" / "config.toml"
        second_file = second / "theater-mode" / "config.toml"

        with patch.dict(os.environ, {"XDG_CONFIG_DIRS": f"{first}:{second}"}):
            self.assertEqual(get_default_system_path(), first_file)

            second_file.write_text("[effect]\ndim_factor = 0.25\n")
            self.assertEqual(get_default_system_path(), second_file)

            first_file.write_text("[effect]\ndim_factor = 0.9\n")
            self.assertEqual(get_default_system_path(), first_file)

    def test_system_config_dirs_normalisation(self) -> None:
        cases = {
            "": ["/etc/xdg"],
            "/a:/b": ["/a", "/b"],
            "/a::/b": ["/a", "/b"],
            "relative:/b": ["/b"],
            "relative": ["/etc/xdg"],
            ":": ["/etc/xdg"],
        }
        for value, expected in cases.items():
            with self.subTest(value=value), patch.dict(os.environ, {"XDG_CONFIG_DIRS": value}):
                self.assertEqual([str(p) for p in system_config_dirs()], expected)

        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual([str(p) for p in system_config_dirs()], ["/etc/xdg"])

    def test_system_layer_found_later_in_the_path_is_actually_applied(self) -> None:
        """Verify config loaded from secondary XDG_CONFIG_DIRS has system provenance."""
        first = self.dir_path / "kdedefaults"
        second = self.dir_path / "etc-xdg"
        for directory in (first, second):
            (directory / "theater-mode").mkdir(parents=True)
        (second / "theater-mode" / "config.toml").write_text("[effect]\ndim_factor = 0.25\n")

        with patch.dict(os.environ, {"XDG_CONFIG_DIRS": f"{first}:{second}"}):
            config, diagnostics = load_resolved_config(
                dev_config=DevConfig(user_config_override=self.user_path)
            )

        self.assertEqual(diagnostics, [])
        self.assertEqual(config.effect.dim_factor, 0.25)
        self.assertEqual(config.provenance["effect.dim_factor"].layer, Layer.SYSTEM)

    def test_reference_config_generation(self) -> None:
        """Test that generated reference config is valid TOML and has no dev keys."""
        ref = generate_reference_config()
        self.assertIn("[effect]", ref)
        self.assertIn("[transition]", ref)
        self.assertIn("[daemon]", ref)
        self.assertNotIn("THEATER_DEV", ref)

        parsed = tomllib.loads(ref)
        self.assertEqual(parsed["effect"]["dim_factor"], 0.85)


if __name__ == "__main__":
    unittest.main()
