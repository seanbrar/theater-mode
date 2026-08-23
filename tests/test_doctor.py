"""Unit tests for the doctor diagnostic command."""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path
from unittest.mock import MagicMock, patch

from theater_mode import __version__, doctor
from theater_mode.client import main as client_main

HEALTHY_ENV = {"XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "KDE"}


def healthy_run(cmd: list[str]) -> tuple[int, str]:
    """A machine where everything is present and working."""
    prog = Path(cmd[0]).name
    if prog == "plasmashell":
        return 0, "plasmashell 6.7.4"
    if prog == "systemctl":
        return 0, "active" if "is-active" in cmd else (0, "enabled")[1]
    if prog == "kreadconfig6":
        return 0, "true"
    if prog in ("theater-dimmer", "theater-art"):
        return 0, f"{prog} {__version__}"
    return -1, ""


def healthy_dbus(method: str, *_args: object) -> str:
    if method == "GetDiagnostics":
        return "[]"
    if method == "GetOutputs":
        return json.dumps([{"connector": "DP-1"}, {"connector": "DP-2"}])
    if method == "GetResolved":
        return json.dumps({"effect": {"dimming": 0.85}, "outputs": {}})
    if method == "Status":
        return json.dumps(
            {
                "effect": "dim",
                "effect_process_running": False,
                "affected_outputs": [],
                "detector_silence_seconds": 5.0,
                "active_output": None,
                "applied_outputs": None,
                "window_count": 0,
            }
        )
    return "running"


class DoctorTestCase(unittest.TestCase):
    def test_plasma_version_check(self) -> None:
        versions = [
            ("plasmashell 6.2.0", doctor.OK),
            ("plasmashell 6.7.4", doctor.OK),
            ("plasmashell 7.0.0", doctor.OK),
            ("plasmashell 6.1.5", doctor.FAIL),
            ("plasmashell 6.0.0", doctor.FAIL),
            ("plasmashell 5.27.10", doctor.FAIL),
        ]
        for output, expected_status in versions:
            with self.subTest(output=output):

                def custom_run(cmd: list[str]) -> tuple[int, str]:
                    if Path(cmd[0]).name == "plasmashell":
                        return 0, output
                    return healthy_run(cmd)

                checks = self.run_checks(run=custom_run)
                self.assertEqual(self.status_of(checks, "Plasma version"), expected_status)

    """Pins every filesystem probe inside a temporary tree so results never vary by host."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.kwin_dir = root / "kwin" / "scripts" / "theater-detect"
        self.kwin_dir.mkdir(parents=True)
        (self.kwin_dir / "metadata.json").write_text("{}")
        self.steam_cache = root / "librarycache"
        self.steam_cache.mkdir()
        (self.steam_cache / "440").mkdir()
        self.art_cache = root / "artcache"
        self.art_cache.mkdir()
        self.kwinrc = root / "kwinrc"

        binary = root / "theater-dimmer"
        binary.write_text("#!/bin/sh\n")
        self.binary = binary

        patches = {
            "KWIN_SCRIPT_DIR": self.kwin_dir,
            "KWIN_CONFIG_FILE": self.kwinrc,
            "STEAM_LIBRARY_CACHES": (self.steam_cache,),
            "ART_CACHE": self.art_cache,
        }
        for name, value in patches.items():
            p = patch.object(doctor, name, value)
            p.start()
            self.addCleanup(p.stop)

        for target in (
            "theater_mode.effects.dim.find_dimmer_binary",
            "theater_mode.steam.find_art_binary",
        ):
            p = patch(target, return_value=binary)
            p.start()
            self.addCleanup(p.stop)

        # The offline fallback reads the real user config, so without this the
        # Configuration check reports whatever is in the developer's home directory.
        p = patch(
            "theater_mode.config.loader.load_resolved_config",
            return_value=(None, []),
        )
        p.start()
        self.addCleanup(p.stop)

        self.addCleanup(self._tmp.cleanup)

    def run_checks(self, **kwargs: object) -> list[doctor.Check]:
        params: dict = {"call_dbus": healthy_dbus, "env": HEALTHY_ENV, "run": healthy_run}
        params.update(kwargs)
        return doctor.run_checks(**params)

    def status_of(self, checks: list[doctor.Check], name: str) -> str:
        return next(c.status for c in checks if c.name == name)


class TestDoctorHealthy(DoctorTestCase):
    def test_healthy_system_reports_no_failures(self) -> None:
        checks = self.run_checks()
        failing = [c.name for c in checks if c.status != doctor.OK]
        self.assertEqual(failing, [])
        self.assertEqual(doctor.exit_code(checks), 0)
        self.assertIn("Everything checks out.", doctor.format_report(checks))

    def test_report_lists_every_section(self) -> None:
        report = doctor.format_report(self.run_checks())
        for section in ("Session", "Installation", "Service", "Daemon", "Artwork"):
            self.assertIn(section, report)

    def test_json_output_is_machine_readable(self) -> None:
        payload = json.loads(doctor.to_json(self.run_checks()))
        self.assertTrue(payload)
        for entry in payload:
            self.assertEqual(set(entry), {"section", "name", "status", "detail", "hint"})
            self.assertIn(entry["status"], {doctor.OK, doctor.WARN, doctor.FAIL})


class TestDoctorFindings(DoctorTestCase):
    def test_x11_session_fails(self) -> None:
        checks = self.run_checks(env={"XDG_SESSION_TYPE": "x11", "XDG_CURRENT_DESKTOP": "KDE"})
        self.assertEqual(self.status_of(checks, "Session type"), doctor.FAIL)
        self.assertEqual(doctor.exit_code(checks), 1)

    def test_game_mode_is_identified_by_name(self) -> None:
        checks = self.run_checks(
            env={"XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "gamescope"}
        )
        desktop = next(c for c in checks if c.name == "Desktop")
        self.assertEqual(desktop.status, doctor.FAIL)
        self.assertIn("Game Mode", desktop.hint)

    def test_missing_session_variables_warn_rather_than_fail(self) -> None:
        checks = self.run_checks(env={})
        self.assertEqual(self.status_of(checks, "Session type"), doctor.WARN)

    def test_operating_system_is_reported(self) -> None:
        checks = self.run_checks()
        os_check = next(c for c in checks if c.name == "Operating system")
        self.assertEqual(os_check.status, doctor.OK)
        self.assertTrue(os_check.detail)

    def test_unreachable_daemon_falls_back_to_offline_checks(self) -> None:
        def dead(_method: str, *_args: object) -> str:
            raise SystemExit(1)

        with patch("theater_mode.display.drm.connected_outputs", return_value={"DP-1", "DP-2"}):
            checks = self.run_checks(call_dbus=dead)

        self.assertEqual(self.status_of(checks, "D-Bus connection"), doctor.FAIL)
        self.assertEqual(self.status_of(checks, "Configuration"), doctor.OK)
        self.assertEqual(self.status_of(checks, "Displays"), doctor.OK)
        self.assertIn("DP-1", next(c for c in checks if c.name == "Displays").detail)

    def test_unreachable_daemon_still_produces_a_full_report(self) -> None:
        def dead(_method: str, *_args: object) -> str:
            raise SystemExit(1)

        checks = self.run_checks(call_dbus=dead)
        self.assertEqual(self.status_of(checks, "D-Bus connection"), doctor.FAIL)
        self.assertIn("Artwork", {c.section for c in checks})
        self.assertEqual(doctor.exit_code(checks), 1)

    def test_daemon_errors_are_surfaced_without_raising(self) -> None:
        def noisy(_method: str, *_args: object) -> str:
            raise RuntimeError("bus not available")

        checks = self.run_checks(call_dbus=noisy)
        self.assertEqual(self.status_of(checks, "D-Bus connection"), doctor.FAIL)

    def test_config_errors_fail_and_warnings_warn(self) -> None:
        def with_diagnostics(payload: list[dict[str, str]]):
            def call(method: str, *_args: object) -> str:
                if method == "GetDiagnostics":
                    return json.dumps(payload)
                return healthy_dbus(method)

            return call

        checks = self.run_checks(call_dbus=with_diagnostics([{"severity": "error"}]))
        self.assertEqual(self.status_of(checks, "Configuration"), doctor.FAIL)

        checks = self.run_checks(call_dbus=with_diagnostics([{"severity": "warning"}]))
        self.assertEqual(self.status_of(checks, "Configuration"), doctor.WARN)

    def test_single_display_warns_because_there_is_nothing_to_dim(self) -> None:
        def one_display(method: str, *_args: object) -> str:
            if method == "GetOutputs":
                return json.dumps([{"connector": "eDP-1"}])
            return healthy_dbus(method)

        checks = self.run_checks(call_dbus=one_display)
        self.assertEqual(self.status_of(checks, "Displays"), doctor.WARN)
        self.assertEqual(doctor.exit_code(checks), 0)

    def test_missing_helper_binary_fails(self) -> None:
        with patch("theater_mode.steam.find_art_binary", return_value=None):
            checks = self.run_checks()
        art = next(c for c in checks if c.name == "theater-art")
        self.assertEqual(art.status, doctor.FAIL)
        self.assertIn("get.sh", art.hint)
        self.assertIn("--build", art.hint)

    def test_helper_that_cannot_run_reports_the_loader_error(self) -> None:
        def broken(cmd: list[str]) -> tuple[int, str]:
            if Path(cmd[0]).name.startswith("theater-"):
                return 1, "version `GLIBC_2.38' not found"
            return healthy_run(cmd)

        checks = self.run_checks(run=broken)
        dimmer = next(c for c in checks if c.name == "theater-dimmer")
        self.assertEqual(dimmer.status, doctor.FAIL)
        self.assertIn("--build", dimmer.hint)

    def test_helper_version_mismatch_warns(self) -> None:
        def stale(cmd: list[str]) -> tuple[int, str]:
            if Path(cmd[0]).name.startswith("theater-"):
                return 0, "theater-dimmer 0.0.1"
            return healthy_run(cmd)

        checks = self.run_checks(run=stale)
        self.assertEqual(self.status_of(checks, "theater-dimmer"), doctor.WARN)

    def test_inactive_service_fails(self) -> None:
        def stopped(cmd: list[str]) -> tuple[int, str]:
            if Path(cmd[0]).name == "systemctl":
                return 0, "inactive" if "is-active" in cmd else "disabled"
            return healthy_run(cmd)

        checks = self.run_checks(run=stopped)
        self.assertEqual(self.status_of(checks, "Unit state"), doctor.FAIL)
        self.assertEqual(self.status_of(checks, "Starts at login"), doctor.WARN)

    def test_missing_systemctl_warns_rather_than_failing(self) -> None:
        def no_systemd(cmd: list[str]) -> tuple[int, str]:
            return (-1, "") if Path(cmd[0]).name == "systemctl" else healthy_run(cmd)

        checks = self.run_checks(run=no_systemd)
        self.assertEqual(self.status_of(checks, "Unit state"), doctor.WARN)

    def test_missing_kwin_script_fails(self) -> None:
        (self.kwin_dir / "metadata.json").unlink()
        checks = self.run_checks()
        self.assertEqual(self.status_of(checks, "KWin script"), doctor.FAIL)

    def test_missing_steam_cache_warns_only(self) -> None:
        with patch.object(doctor, "STEAM_LIBRARY_CACHES", (self.steam_cache / "absent",)):
            checks = self.run_checks()
        self.assertEqual(self.status_of(checks, "Steam library cache"), doctor.WARN)
        self.assertEqual(doctor.exit_code(checks), 0)


class TestKwinScriptDetection(DoctorTestCase):
    def test_reads_kreadconfig_output(self) -> None:
        for value, expected in (("true", True), ("false", False), ("TRUE", True)):
            with self.subTest(value=value):
                self.assertIs(doctor._kwin_script_enabled(lambda _c: (0, value)), expected)

    def test_falls_back_to_parsing_kwinrc(self) -> None:
        self.kwinrc.write_text(
            "[Effect-blur]\ntheater-detectEnabled=true\n\n[Plugins]\ntheater-detectEnabled=true\n"
        )
        self.assertIs(doctor._kwin_script_enabled(lambda _c: (-1, "")), True)

    def test_handles_whitespace_around_equals(self) -> None:
        self.kwinrc.write_text("[Plugins]\n  theater-detectEnabled = true \n")
        self.assertIs(doctor._kwin_script_enabled(lambda _c: (-1, "")), True)

    def test_key_outside_the_plugins_group_does_not_count(self) -> None:
        self.kwinrc.write_text("[Effect-blur]\ntheater-detectEnabled=true\n")
        self.assertIs(doctor._kwin_script_enabled(lambda _c: (-1, "")), False)

    def test_absent_kwinrc_is_indeterminate(self) -> None:
        self.assertIsNone(doctor._kwin_script_enabled(lambda _c: (-1, "")))


class TestTildeAbbreviation(unittest.TestCase):
    def test_empty_string_is_empty(self) -> None:
        self.assertEqual(doctor._tilde(""), "")

    def test_home_is_abbreviated(self) -> None:
        self.assertEqual(doctor._tilde(Path.home() / "a" / "b"), "~/a/b")

    def test_home_itself_is_just_tilde(self) -> None:
        self.assertEqual(doctor._tilde(Path.home()), "~")

    def test_paths_outside_home_are_untouched(self) -> None:
        self.assertEqual(doctor._tilde("/usr/bin/theater-art"), "/usr/bin/theater-art")


class TestDoctorCliRouting(DoctorTestCase):
    def _run(self, argv: list[str]) -> tuple[int, str]:
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = client_main(argv, call_dbus=MagicMock(side_effect=healthy_dbus))
        return code, stdout.getvalue()

    def test_doctor_prints_a_report_and_returns_zero(self) -> None:
        # Collected before patching: run_checks is what gets replaced.
        checks = self.run_checks()
        with patch.object(doctor, "run_checks", return_value=checks):
            code, out = self._run(["doctor"])
        self.assertEqual(code, 0)
        self.assertIn("Session", out)

    def test_doctor_json_flag_emits_json(self) -> None:
        checks = self.run_checks()
        with patch.object(doctor, "run_checks", return_value=checks):
            code, out = self._run(["doctor", "--json"])
        self.assertEqual(code, 0)
        self.assertIsInstance(json.loads(out), list)

    def test_doctor_exit_code_reflects_failures(self) -> None:
        failing = [doctor.Check("Session", "Session type", doctor.FAIL, "x11", "hint")]
        with patch.object(doctor, "run_checks", return_value=failing):
            code, _ = self._run(["doctor"])
        self.assertEqual(code, 1)

    def test_stopped_effect_helper_reports_failure_only_when_outputs_are_active(self) -> None:
        def with_stopped_helper(method: str, *_args: object) -> str:
            if method == "Status":
                return json.dumps(
                    {
                        "effect": "dim",
                        "effect_process_running": False,
                        "affected_outputs": ["DP-2"],
                    }
                )
            return healthy_dbus(method)

        checks = self.run_checks(call_dbus=with_stopped_helper)
        self.assertEqual(self.status_of(checks, "Display effect helper"), doctor.FAIL)

        idle_checks = self.run_checks(call_dbus=healthy_dbus)
        self.assertNotIn("Display effect helper", [check.name for check in idle_checks])

    def test_zero_dimming_uses_only_matching_connected_display_rules(self) -> None:
        def zero_with_stale_override(method: str, *_args: object) -> str:
            if method == "GetResolved":
                return json.dumps(
                    {
                        "effect": {"dimming": 0.0},
                        "outputs": {"DP-9": {"dimming": 0.8}},
                    }
                )
            return healthy_dbus(method)

        checks = self.run_checks(call_dbus=zero_with_stale_override)
        self.assertEqual(self.status_of(checks, "Dimming"), doctor.WARN)

    def test_zero_dimming_detects_connected_output_overrides(self) -> None:
        def nullified_outputs(method: str, *_args: object) -> str:
            if method == "GetResolved":
                return json.dumps(
                    {
                        "effect": {"dimming": 0.85},
                        "outputs": {
                            "DP-1": {"dimming": 0.0},
                            "DP-2": {"dimming": 0.0},
                        },
                    }
                )
            return healthy_dbus(method)

        checks = self.run_checks(call_dbus=nullified_outputs)
        self.assertEqual(self.status_of(checks, "Dimming"), doctor.WARN)

    def test_unreadable_status_is_reported(self) -> None:
        def bad_status(method: str, *_args: object) -> str:
            if method == "Status":
                return "not json"
            return healthy_dbus(method)

        checks = self.run_checks(call_dbus=bad_status)
        self.assertEqual(self.status_of(checks, "Status report"), doctor.FAIL)

    def test_kwin_detector_contact_checks(self) -> None:
        def silent_for(seconds: float) -> Callable[..., str]:
            def call_dbus(method: str, *_args: object) -> str:
                if method == "Status":
                    return json.dumps(
                        {
                            "effect": "dim",
                            "effect_process_running": False,
                            "affected_outputs": [],
                            "detector_silence_seconds": seconds,
                        }
                    )
                return healthy_dbus(method)

            return call_dbus

        checks = self.run_checks(call_dbus=silent_for(0.0))
        self.assertEqual(self.status_of(checks, "KWin detector contact"), doctor.OK)

        checks = self.run_checks(call_dbus=silent_for(90.0))
        self.assertEqual(self.status_of(checks, "KWin detector contact"), doctor.FAIL)

        checks = self.run_checks(call_dbus=healthy_dbus)
        self.assertEqual(self.status_of(checks, "KWin detector contact"), doctor.OK)


if __name__ == "__main__":
    unittest.main()
