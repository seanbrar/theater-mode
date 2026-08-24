"""Tests for developer-tool shell entry points."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NESTED_TOOL = ROOT / "tools" / "nested" / "nested-session.sh"
VM_TOOL = ROOT / "tools" / "vm" / "vm.sh"


class VmToolTests(unittest.TestCase):
    """Exercise validation that must run before optional VM dependencies."""

    def test_clean_rejects_broad_state_directories(self) -> None:
        for state_dir in ("/", "/tmp", "/var/tmp", str(Path.home()), str(ROOT)):
            with self.subTest(state_dir=state_dir):
                result = subprocess.run(
                    [VM_TOOL, "clean"],
                    env={**os.environ, "THEATER_VM_STATE_DIR": state_dir},
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("must name a dedicated directory", result.stderr)

    def test_clean_rejects_unrecognized_nonempty_state_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state_dir = Path(temporary)
            (state_dir / "unrelated").touch()
            result = subprocess.run(
                [VM_TOOL, "clean"],
                env={**os.environ, "THEATER_VM_STATE_DIR": str(state_dir)},
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertTrue((state_dir / "unrelated").exists())
            self.assertIn("refusing to clean an unrecognized state directory", result.stderr)

    def test_clean_removes_marker_on_prepared_state_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state_dir = Path(temporary) / "vm_state"
            state_dir.mkdir()
            marker = state_dir / ".theater-mode-vm-state"
            marker.touch()
            (state_dir / "golden.qcow2").touch()

            result = subprocess.run(
                [VM_TOOL, "clean"],
                env={**os.environ, "THEATER_VM_STATE_DIR": str(state_dir)},
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0)
            self.assertFalse(marker.exists())
            self.assertFalse((state_dir / "golden.qcow2").exists())


class NestedToolTests(unittest.TestCase):
    """Exercise nested-session argument parsing before graphical dependencies."""

    def test_equals_syntax_is_recognized(self) -> None:
        result = subprocess.run(
            [NESTED_TOOL, "--profile=does-not-exist"],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no such profile", result.stderr)
        self.assertNotIn("unknown option", result.stderr)

    def test_empty_equals_values_are_rejected(self) -> None:
        for option in ("game", "config", "showcase"):
            with self.subTest(option=option):
                result = subprocess.run(
                    [NESTED_TOOL, f"--{option}="],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(f"--{option} requires a value", result.stderr)

    def test_invalid_appid_is_rejected(self) -> None:
        for appid in ("0", "abc", "-1"):
            with self.subTest(appid=appid):
                result = subprocess.run(
                    [NESTED_TOOL, f"--appid={appid}"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("invalid AppID", result.stderr)

    def test_invalid_geometry_is_rejected(self) -> None:
        for geometry in ("0x0", "invalid", "100", "0x1080"):
            with self.subTest(geometry=geometry):
                result = subprocess.run(
                    [NESTED_TOOL, f"--geometry={geometry}"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("invalid geometry", result.stderr)


if __name__ == "__main__":
    unittest.main()
