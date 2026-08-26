"""Tests for the release bootstrap installer."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
import tarfile
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = "seanbrar/theater-mode"
VERSION = "9.8.7"
LATEST_PATH = f"/repos/{REPO}/releases/latest"
TAG_PATH = f"/repos/{REPO}/releases/tags/v{VERSION}"
ARCHIVE_PATH = "/download/archive"
CHECKSUM_PATH = "/download/checksum"


class _FixtureHandler(BaseHTTPRequestHandler):
    """Serve HTTP responses configured in the test route table."""

    def do_GET(self) -> None:
        status, body = self.server.routes.get(self.path, (404, b'{"message": "Not Found"}'))
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        """Suppress HTTP server logging to stderr."""


class BootstrapFixture(unittest.TestCase):
    """Publish a loopback release archive to test get.sh."""

    def setUp(self) -> None:
        """Publish a minimal release payload and populate fake CLI dependencies."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.fake_bin = self.root / "bin"
        self.fake_bin.mkdir()
        self.home = self.root / "home"
        self.home.mkdir()
        self.log = self.root / "gh.log"
        self.curl_log = self.root / "curl.log"

        name = f"theater-mode-v{VERSION}-linux-{platform.machine()}"
        tree = self.root / name
        tree.mkdir()
        installer = tree / "install.sh"
        installer.write_text("#!/usr/bin/env bash\nprintf 'installed:%s\\n' \"$*\"\n")
        installer.chmod(0o755)

        archive = self.root / f"{name}.tar.gz"
        with tarfile.open(archive, "w:gz") as release:
            release.add(tree, arcname=name)
        self.archive_bytes = archive.read_bytes()
        digest = hashlib.sha256(self.archive_bytes).hexdigest()
        self.checksum_bytes = f"{digest}  {name}.tar.gz\n".encode()

        # api.github.com URLs pass get.sh's HTTPS filter before the shim redirects them.
        metadata = json.dumps(
            {
                "tag_name": f"v{VERSION}",
                "assets": [
                    {
                        "name": f"{name}.tar.gz",
                        "browser_download_url": f"https://api.github.com{ARCHIVE_PATH}",
                    },
                    {
                        "name": f"{name}.tar.gz.sha256",
                        "browser_download_url": f"https://api.github.com{CHECKSUM_PATH}",
                    },
                ],
            }
        ).encode()

        self.routes = {
            LATEST_PATH: (200, metadata),
            TAG_PATH: (200, metadata),
            ARCHIVE_PATH: (200, self.archive_bytes),
            CHECKSUM_PATH: (200, self.checksum_bytes),
        }
        server = ThreadingHTTPServer(("127.0.0.1", 0), _FixtureHandler)
        server.routes = self.routes
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.api_base = f"http://127.0.0.1:{server.server_address[1]}"

        # Redirect api.github.com to the loopback fixture.
        self._write_executable(
            "python3",
            """#!/usr/bin/env bash
prefix="https://api.github.com"
args=()
for arg in "$@"; do
    [ "${arg#"$prefix"}" = "$arg" ] || arg="$GET_API_BASE${arg#"$prefix"}"
    args+=("$arg")
done
exec "$GET_REAL_PYTHON" "${args[@]}"
""",
        )
        self._write_executable(
            "curl",
            """#!/usr/bin/env bash
printf 'called\n' >> "$CURL_LOG"
exit 1
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

    def _write_executable(self, name: str, contents: str) -> None:
        """Write one fake command into the fixture PATH."""
        path = self.fake_bin / name
        path.write_text(contents)
        path.chmod(0o755)

    def _run_get(
        self, verify_rc: int, *args: str, has_attestation: bool = True
    ) -> subprocess.CompletedProcess[str]:
        """Run `get.sh` against a fake `gh` with a chosen capability and result."""
        env = os.environ.copy()
        env.pop("THEATER_MODE_REPO", None)
        env.update(
            {
                "HOME": str(self.home),
                "PATH": f"{self.fake_bin}:{env['PATH']}",
                "GET_API_BASE": self.api_base,
                "GET_REAL_PYTHON": sys.executable,
                "CURL_LOG": str(self.curl_log),
                "GH_HAS_ATTESTATION": "0" if has_attestation else "1",
                "GH_VERIFY_RC": str(verify_rc),
                "GH_LOG": str(self.log),
            }
        )
        return subprocess.run(
            [str(Path(__file__).resolve().parents[1] / "get.sh"), *args],
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )


class GetScriptTests(BootstrapFixture):
    """Verify release selection, attestation, and install.sh delegation."""

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
        self.assertFalse(self.curl_log.exists(), "get.sh unexpectedly invoked curl")

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

    def test_checksum_mismatch_stops_the_install(self) -> None:
        self.routes[CHECKSUM_PATH] = (200, b"0" * 64 + b"  release.tar.gz\n")

        result = self._run_get(0)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not match its published checksum", result.stderr)
        self.assertNotIn("installed", result.stdout)

    def test_exact_release_is_selected_and_installer_arguments_are_forwarded(self) -> None:
        """Verify --release is consumed and remaining options are passed to install.sh."""
        result = self._run_get(0, "--release", f"v{VERSION}", "--no-service")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"Looking up theater-mode v{VERSION}", result.stdout)
        self.assertIn("installed:--no-service", result.stdout)

    def test_exact_release_accepts_equals_syntax(self) -> None:
        result = self._run_get(0, f"--release={VERSION}")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"Looking up theater-mode v{VERSION}", result.stdout)

    def test_invalid_exact_release_is_rejected_before_download(self) -> None:
        """The rejection quotes the version as typed, tag prefix and all."""
        for args, typed in ((("--release", "../../latest"), "../../latest"), (("--release=",), "")):
            with self.subTest(args=args):
                result = self._run_get(0, *args)

                self.assertNotEqual(result.returncode, 0)
                self.assertIn(f"invalid release version: {typed}\n", result.stderr)
                self.assertNotIn("Looking up", result.stdout)

    def test_a_rejected_version_is_quoted_as_typed(self) -> None:
        result = self._run_get(0, "--release", f"vv{VERSION}")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn(f"invalid release version: vv{VERSION}\n", result.stderr)


class GetScriptFailureTests(BootstrapFixture):
    """Verify error messages for failed release lookups and downloads."""

    def test_missing_exact_release(self) -> None:
        result = self._run_get(0, "--release", "9.8.8")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("release v9.8.8 was not found on GitHub", result.stderr)
        self.assertEqual(result.stderr.count("error:"), 1)

    def test_missing_stable_release(self) -> None:
        self.routes[LATEST_PATH] = (404, b'{"message": "Not Found"}')

        result = self._run_get(0)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn(f"no stable releases were found for {REPO}", result.stderr)

    def test_rate_limited_lookup(self) -> None:
        self.routes[LATEST_PATH] = (403, b'{"message": "rate limit exceeded"}')

        result = self._run_get(0)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("rate-limiting this address", result.stderr)

    def test_server_error_reports_its_status(self) -> None:
        self.routes[LATEST_PATH] = (500, b"boom")

        result = self._run_get(0)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("could not reach GitHub (500", result.stderr)

    def test_unreachable_github_is_never_reported_as_a_status(self) -> None:
        self.api_base = "http://127.0.0.1:1"

        result = self._run_get(0)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("could not reach GitHub:", result.stderr)
        self.assertNotIn("HTTP", result.stderr)

    def test_malformed_metadata_is_diagnosed_once(self) -> None:
        self.routes[LATEST_PATH] = (200, b"not json")

        result = self._run_get(0)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid release metadata", result.stderr)
        self.assertEqual(result.stderr.count("error:"), 1)

    def _publish(self, tag: object, archive_url: str) -> None:
        """Replace the release metadata with a chosen tag and archive URL."""
        name = f"theater-mode-v{VERSION}-linux-{platform.machine()}.tar.gz"
        self.routes[LATEST_PATH] = (
            200,
            json.dumps(
                {
                    "tag_name": tag,
                    "assets": [
                        {"name": name, "browser_download_url": archive_url},
                        {
                            "name": f"{name}.sha256",
                            "browser_download_url": f"https://api.github.com{CHECKSUM_PATH}",
                        },
                    ],
                }
            ).encode(),
        )

    def test_a_non_https_asset_url_is_refused(self) -> None:
        """Reject release assets whose download URLs are not HTTPS."""
        self._publish(f"v{VERSION}", f"http://127.0.0.1{ARCHIVE_PATH}")

        result = self._run_get(0)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn(f"release v{VERSION} has no build for", result.stderr)

    def test_a_multi_line_metadata_value_is_refused(self) -> None:
        """A newline in any field would be read as the next field, past the HTTPS filter."""
        self._publish(
            f"v{VERSION}\nhttps://attacker.invalid/payload", f"https://api.github.com{ARCHIVE_PATH}"
        )

        result = self._run_get(0)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("value that spans lines", result.stderr)
        self.assertNotIn("attacker.invalid", result.stderr)

    def test_an_unusable_tag_is_reported_as_a_missing_version(self) -> None:
        for tag in (None, 7, "", "v"):
            with self.subTest(tag=tag):
                self._publish(tag, f"https://api.github.com{ARCHIVE_PATH}")

                result = self._run_get(0)

                self.assertNotEqual(result.returncode, 0)
                self.assertIn("a release with no version tag", result.stderr)
                self.assertNotIn("vNone", result.stderr)

    def test_missing_archive_asset(self) -> None:
        del self.routes[ARCHIVE_PATH]

        result = self._run_get(0)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            f"release v{VERSION} has no archive for {platform.machine()} on GitHub",
            result.stderr,
        )

    def test_missing_checksum_asset(self) -> None:
        del self.routes[CHECKSUM_PATH]

        result = self._run_get(0)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            f"release v{VERSION} has no checksum for {platform.machine()} on GitHub",
            result.stderr,
        )


if __name__ == "__main__":
    unittest.main()
