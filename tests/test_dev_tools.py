"""Tests for developer-tool shell entry points."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NESTED_TOOL = ROOT / "tools" / "nested" / "nested-session.sh"
BUILD_TOOL = ROOT / "tools" / "runner" / "release-build.sh"
NOTES_TOOL = ROOT / "tools" / "runner" / "release-notes.sh"
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


class ReleaseNotesToolTests(unittest.TestCase):
    """Exercise the composed release body against fixture changelogs."""

    CHANGELOG = """\
# Changelog

How to read this file.

## 2.0.0

Artwork now loads for games with custom library images.

## 1.0.0

The first release.
"""

    def _compose(self, tag: str, changelog: str | None = None) -> subprocess.CompletedProcess:
        """Run the composer in a throwaway repository root holding the given changelog."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "tools" / "runner").mkdir(parents=True)
            script = root / "tools" / "runner" / "release-notes.sh"
            shutil.copy(NOTES_TOOL, script)
            (root / "CHANGELOG.md").write_text(self.CHANGELOG if changelog is None else changelog)
            return subprocess.run(
                [script, tag],
                env={**os.environ, "GITHUB_REPOSITORY": "example/project"},
                capture_output=True,
                text=True,
                check=False,
            )

    def test_a_prerelease_names_the_version_in_every_command(self) -> None:
        """A tester copies one command, so neither may fall back to the stable path."""
        result = self._compose("v2.0.0-beta.1")

        self.assertEqual(result.returncode, 0)
        self.assertIn("theater-mode update --release 2.0.0-beta.1", result.stdout)
        self.assertIn("bash -s -- --release 2.0.0-beta.1", result.stdout)
        self.assertNotIn("\ntheater-mode update\n", result.stdout)

    def test_a_preview_describes_the_release_it_previews(self) -> None:
        """Otherwise the stable release restates every preview, and links only to the last."""
        result = self._compose("v2.0.0-rc.1")

        self.assertEqual(result.returncode, 0)
        self.assertIn("custom library images", result.stdout)
        self.assertIn("example/project/compare/v1.0.0...v2.0.0-rc.1", result.stdout)

    def test_a_stable_release_names_no_version(self) -> None:
        result = self._compose("v2.0.0")

        self.assertEqual(result.returncode, 0)
        self.assertIn("\ntheater-mode update\n", result.stdout)
        self.assertNotIn("--release", result.stdout)

    def test_the_section_stops_at_the_next_release(self) -> None:
        result = self._compose("v2.0.0")

        self.assertIn("custom library images", result.stdout)
        self.assertNotIn("The first release.", result.stdout)
        self.assertNotIn("How to read this file.", result.stdout)

    def test_the_compare_link_names_the_preceding_release(self) -> None:
        result = self._compose("v2.0.0")

        self.assertIn("example/project/compare/v1.0.0...v2.0.0", result.stdout)

    def test_a_first_release_has_no_compare_link(self) -> None:
        result = self._compose("v1.0.0", "# Changelog\n\n## 1.0.0\n\nThe first release.\n")

        self.assertEqual(result.returncode, 0)
        self.assertNotIn("compare", result.stdout)

    def test_a_missing_section_refuses_and_names_a_pushed_tag(self) -> None:
        """An empty heading is still a missing section, and its own tag does not exist yet."""
        result = self._compose(
            "v2.0.0", "# Changelog\n\n## 2.0.0\n\n## 1.0.0\n\nThe first release.\n"
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn('v2.0.0 needs a "## 2.0.0" section', result.stderr)
        self.assertIn("git log v1.0.0..HEAD", result.stderr)
        self.assertNotIn("v2.0.0..", result.stderr)

    def test_a_whitespace_only_section_is_missing(self) -> None:
        result = self._compose(
            "v2.0.0", "# Changelog\n\n## 2.0.0\n \t \n## 1.0.0\n\nThe first release.\n"
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn('v2.0.0 needs a "## 2.0.0" section', result.stderr)

    def test_release_sections_must_be_newest_first(self) -> None:
        result = self._compose(
            "v2.0.0",
            "# Changelog\n\n## 1.0.0\n\nThe first release.\n\n## 2.0.0\n\nSecond.\n",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("release sections must be newest first", result.stderr)

    def test_release_sections_must_be_unique(self) -> None:
        result = self._compose(
            "v2.0.0", "# Changelog\n\n## 2.0.0\n\nFirst.\n\n## 2.0.0\n\nSecond.\n"
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("without duplicates", result.stderr)

    def test_a_preview_heading_is_rejected(self) -> None:
        """One section per stable release is what anchors a compare link at the last stable."""
        result = self._compose(
            "v2.0.0-beta.1", "# Changelog\n\n## 2.0.0-beta.1\n\nA.\n\n## 1.0.0\n\nB.\n"
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsupported release heading in CHANGELOG.md: ## 2.0.0-beta.1", result.stderr)

    def test_a_preview_is_refused_by_the_section_it_needs(self) -> None:
        """The tag and the section it requires differ, so the message has to name both."""
        result = self._compose("v3.0.0-alpha.1")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn('v3.0.0-alpha.1 needs a "## 3.0.0" section', result.stderr)

    def test_unsupported_versions_are_rejected(self) -> None:
        for tag in ("v1.2", "v1.2.3-preview.1", "vlatest"):
            with self.subTest(tag=tag):
                result = self._compose(tag)

                self.assertNotEqual(result.returncode, 0)
                self.assertIn("unsupported release version", result.stderr)

    def test_release_tags_require_the_v_prefix(self) -> None:
        result = self._compose("2.0.0")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("release tag must start with v", result.stderr)


class ReleaseBuildToolTests(unittest.TestCase):
    """Exercise the tag guards that run before any build dependency is needed."""

    def test_release_tags_require_the_v_prefix(self) -> None:
        """Without this guard the version equality check reports a version as not matching itself."""
        with tempfile.TemporaryDirectory() as temporary:
            result = subprocess.run(
                [BUILD_TOOL, temporary, "0.0.0"],
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("release tag must start with v", result.stderr)


if __name__ == "__main__":
    unittest.main()
