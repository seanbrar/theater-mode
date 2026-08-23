"""Tests for the release bootstrap installer."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path


class GetScriptTests(unittest.TestCase):
    """Exercise provenance verification at the shell-script boundary."""

    def setUp(self) -> None:
        """Create a minimal valid release and fake GitHub-facing commands."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.fake_bin = self.root / "bin"
        self.fake_bin.mkdir()
        self.home = self.root / "home"
        self.home.mkdir()
        self.log = self.root / "gh.log"

        version = "9.8.7"
        name = f"theater-mode-v{version}-linux-{platform.machine()}"
        tree = self.root / name
        tree.mkdir()
        installer = tree / "install.sh"
        installer.write_text("#!/usr/bin/env bash\nprintf 'installed\\n'\n")
        installer.chmod(0o755)

        self.archive = self.root / f"{name}.tar.gz"
        with tarfile.open(self.archive, "w:gz") as release:
            release.add(tree, arcname=name)
        digest = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        self.checksum = self.root / f"{name}.tar.gz.sha256"
        self.checksum.write_text(f"{digest}  {self.archive.name}\n")
        self.metadata = self.root / "release.json"
        self.metadata.write_text(
            json.dumps(
                {
                    "tag_name": f"v{version}",
                    "assets": [
                        {
                            "name": self.archive.name,
                            "browser_download_url": "https://example.test/archive",
                        },
                        {
                            "name": self.checksum.name,
                            "browser_download_url": "https://example.test/checksum",
                        },
                    ],
                }
            )
        )

        self._write_executable(
            "curl",
            """#!/usr/bin/env bash
url="${*: -1}"
case "$url" in
    */releases/latest) exec /usr/bin/cat "$GET_METADATA" ;;
    https://example.test/archive) exec /usr/bin/cat "$GET_ARCHIVE" ;;
    https://example.test/checksum) exec /usr/bin/cat "$GET_CHECKSUM" ;;
    *) exit 22 ;;
esac
""",
        )
        self._write_executable(
            "gh",
            """#!/usr/bin/env bash
if [ "${1:-} ${2:-} ${3:-}" = "attestation verify --help" ]; then
    exit "$GH_HAS_ATTESTATION"
else
    printf 'called\n' > "$GH_LOG"
    printf 'fake gh refused to verify\n' >&2
    exit "$GH_VERIFY_RC"
fi
""",
        )

    def tearDown(self) -> None:
        """Remove the temporary release fixture."""
        self.temp_dir.cleanup()

    def _write_executable(self, name: str, contents: str) -> None:
        """Write one fake command into the fixture PATH."""
        path = self.fake_bin / name
        path.write_text(contents)
        path.chmod(0o755)

    def _run_get(
        self, verify_rc: int, *, has_attestation: bool = True
    ) -> subprocess.CompletedProcess[str]:
        """Run `get.sh` against a fake `gh` with a chosen capability and result."""
        env = os.environ.copy()
        env.pop("THEATER_MODE_REPO", None)
        env.update(
            {
                "HOME": str(self.home),
                "PATH": f"{self.fake_bin}:{env['PATH']}",
                "GET_METADATA": str(self.metadata),
                "GET_ARCHIVE": str(self.archive),
                "GET_CHECKSUM": str(self.checksum),
                "GH_HAS_ATTESTATION": "0" if has_attestation else "1",
                "GH_VERIFY_RC": str(verify_rc),
                "GH_LOG": str(self.log),
            }
        )
        return subprocess.run(
            [str(Path(__file__).resolve().parents[1] / "get.sh")],
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_gh_without_attestation_command_falls_back(self) -> None:
        """Install on the checksum alone when `gh` cannot verify attestations.

        A host holding no `gh` at all takes this same branch, so this covers the
        checksum-only install end to end rather than only the skipped verification.
        """
        result = self._run_get(0, has_attestation=False)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("checksum verified", result.stdout)
        self.assertNotIn("build provenance verified", result.stdout)
        self.assertIn("installed", result.stdout)
        self.assertFalse(self.log.exists())

    def test_verified_provenance_is_reported(self) -> None:
        """Report provenance only after `gh` verifies it."""
        result = self._run_get(0)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("build provenance verified", result.stdout)
        self.assertTrue(self.log.exists())

    def test_failed_verification_stops_and_quotes_gh(self) -> None:
        """Refuse the archive, and surface what `gh` said about it."""
        result = self._run_get(1)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("could not confirm this download was built by", result.stderr)
        self.assertIn("fake gh refused to verify", result.stderr)
        self.assertNotIn("installed", result.stdout)

    def test_unauthenticated_gh_falls_back(self) -> None:
        """Keep installation frictionless when `gh` requests authentication."""
        result = self._run_get(4)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("build provenance verified", result.stdout)
        self.assertIn("installed", result.stdout)


if __name__ == "__main__":
    unittest.main()
