"""Tests for release discovery, version ordering, and checksum enforcement."""

from __future__ import annotations

import hashlib
import http.client
import io
import json
import re
import unittest
import urllib.error
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from theater_mode import update


class VersionOrderingTests(unittest.TestCase):
    def test_orders_numerically_not_lexically(self):
        self.assertTrue(update.is_newer("1.10.0", "1.9.0"))
        self.assertFalse(update.is_newer("1.9.0", "1.10.0"))

    def test_equal_versions_are_not_newer(self):
        self.assertFalse(update.is_newer("1.0.0", "1.0.0"))

    def test_leading_v_is_ignored(self):
        self.assertTrue(update.is_newer("v2.0.0", "1.0.0"))
        self.assertFalse(update.is_newer("v1.0.0", "1.0.0"))

    def test_shorter_versions_compare_against_longer(self):
        self.assertTrue(update.is_newer("1.1", "1.0.9"))
        self.assertFalse(update.is_newer("1.0", "1.0.1"))
        self.assertFalse(update.is_newer("1.0.0", "1.0"))

    def test_prereleases_precede_the_final_release(self):
        self.assertTrue(update.is_newer("1.2.0-rc1", "1.1.0"))
        self.assertTrue(update.is_newer("1.2.0", "1.2.0-rc1"))
        self.assertFalse(update.is_newer("1.2.0-rc1", "1.2.0"))

    def test_invalid_versions_are_not_updates(self):
        self.assertFalse(update.is_newer("garbage", "1.0.0"))


def _release_payload(version="2.0.0", arch="x86_64", with_checksum=True):
    name = f"theater-mode-v{version}-linux-{arch}.tar.gz"
    assets = [{"name": name, "browser_download_url": f"https://example.invalid/{name}"}]
    if with_checksum:
        assets.append(
            {
                "name": name + ".sha256",
                "browser_download_url": f"https://example.invalid/{name}.sha256",
            }
        )
    return json.dumps({"tag_name": f"v{version}", "assets": assets}).encode()


class ReleaseGrammarTests(unittest.TestCase):
    """Verify release version regexes match across shell and Python implementations."""

    SHELL_SOURCES = ("get.sh", "tools/runner/release-build.sh")
    SAMPLES = (
        "1.2.3",
        "0.0.0",
        "10.20.30",
        "1.2.3-alpha.0",
        "1.2.3-beta.11",
        "1.2.3-rc.1",
        "1.2.3-exp.1",
        "1.2",
        "1.2.3.4",
        "01.2.3",
        "1.2.3-alpha",
        "1.2.3-alpha.01",
        "1.2.3-preview.1",
        "1.2.3+local",
        "v1.2.3",
        "1\u0662.2.3",
        "1.2.3-beta.1\u0662",
        "../../latest",
        "",
    )

    def _shell_pattern(self, name: str) -> re.Pattern[str]:
        """Extract the release version regex from a shell script."""
        source = Path(__file__).resolve().parents[1] / name
        match = re.search(r"=~\s+(\S+)\s+\]\]", source.read_text())
        self.assertIsNotNone(match, f"{name} has no release version test")
        return re.compile(match.group(1))

    def test_a_hyphen_marks_exactly_the_prereleases(self):
        """release.yml derives prerelease status from `contains(github.ref_name, '-')`."""
        for sample in self.SAMPLES:
            if update._PUBLISHED_VERSION_RE.fullmatch(sample):
                with self.subTest(version=sample):
                    self.assertEqual("-" in sample, update.is_prerelease(sample))

    def test_every_copy_of_the_grammar_agrees(self):
        for name in self.SHELL_SOURCES:
            pattern = self._shell_pattern(name)
            for sample in self.SAMPLES:
                with self.subTest(source=name, version=sample):
                    self.assertEqual(
                        pattern.fullmatch(sample) is not None,
                        update._PUBLISHED_VERSION_RE.fullmatch(sample) is not None,
                    )


class CheckTests(unittest.TestCase):
    def _check(self, version: str, latest: str) -> str:
        stream = io.StringIO()
        with (
            patch.object(update, "__version__", version),
            patch.object(update, "fetch_latest", return_value=update.Release(latest, "u", "c")),
        ):
            self.assertEqual(update.check(stream=stream), 0)
        return stream.getvalue()

    def test_reports_an_available_update(self):
        output = self._check("1.0.0", "1.1.0")
        self.assertIn("1.1.0 is available", output)

    def test_reports_being_current(self):
        self.assertIn("is up to date", self._check("1.0.0", "1.0.0"))

    def test_names_the_way_back_from_a_testing_build(self):
        output = self._check("1.1.0-beta.1", "1.0.0")
        self.assertIn("is a testing build", output)
        self.assertIn("theater-mode update --release 1.0.0", output)


class AssetSelectionTests(unittest.TestCase):
    def test_selects_the_artifact_for_this_machine(self):
        with (
            patch.object(update, "_get", return_value=_release_payload()),
            patch("platform.machine", return_value="x86_64"),
        ):
            release = update.fetch_latest()
        self.assertEqual(release.version, "2.0.0")
        self.assertTrue(release.tarball_url.endswith("linux-x86_64.tar.gz"))
        self.assertTrue(release.checksum_url.endswith(".sha256"))

    def test_reports_no_artifact_for_a_foreign_architecture(self):
        with (
            patch.object(update, "_get", return_value=_release_payload(arch="x86_64")),
            patch("platform.machine", return_value="aarch64"),
        ):
            release = update.fetch_latest()
        self.assertIsNone(release.tarball_url)

    def test_checksum_asset_is_not_mistaken_for_the_tarball(self):
        with (
            patch.object(update, "_get", return_value=_release_payload()),
            patch("platform.machine", return_value="x86_64"),
        ):
            release = update.fetch_latest()
        self.assertFalse(release.tarball_url.endswith(".sha256"))

    def test_non_https_asset_urls_are_ignored(self):
        payload = json.dumps(
            {
                "tag_name": "v2.0.0",
                "assets": [
                    {
                        "name": "theater-mode-v2.0.0-linux-x86_64.tar.gz",
                        "browser_download_url": "file:///etc/passwd",
                    },
                    {
                        "name": "theater-mode-v2.0.0-linux-x86_64.tar.gz.sha256",
                        "browser_download_url": "http://example.invalid/x.sha256",
                    },
                ],
            }
        ).encode()
        with (
            patch.object(update, "_get", return_value=payload),
            patch("platform.machine", return_value="x86_64"),
        ):
            release = update.fetch_latest()
        self.assertIsNone(release.tarball_url)
        self.assertIsNone(release.checksum_url)

    def test_get_refuses_a_non_https_url(self):
        with self.assertRaises(update.UpdateError) as caught:
            update._get("file:///etc/passwd", not_found="unused")
        self.assertIn("non-HTTPS", str(caught.exception))

    def test_missing_tag_is_an_error(self):
        with patch.object(update, "_get", return_value=json.dumps({"assets": []}).encode()):
            with self.assertRaises(update.UpdateError):
                update.fetch_latest()

    def test_an_unusable_tag_is_not_treated_as_a_version(self):
        """A JSON value that is not a plain tag string must not become part of a filename."""
        for tag in (None, 7, ["v1.2.3"], {"v": 1}, "", "v"):
            with self.subTest(tag=tag):
                with patch.object(
                    update, "_get", return_value=json.dumps({"tag_name": tag}).encode()
                ):
                    with self.assertRaises(update.UpdateError) as caught:
                        update.fetch_latest()
                self.assertEqual(str(caught.exception), "the release has no version tag")

    def test_repeated_v_prefixes_do_not_satisfy_the_requested_tag(self):
        """Only one `v` is a tag prefix; the rest are part of the version being compared."""
        with patch.object(update, "_get", return_value=_release_payload("vv1.2.3")):
            with self.assertRaises(update.UpdateError) as caught:
                update.fetch_release("1.2.3")
        self.assertIn("refusing to install", str(caught.exception))

    def test_a_truncated_response_is_a_user_facing_error(self):
        response = MagicMock()
        response.__enter__.return_value.read.side_effect = http.client.IncompleteRead(b"partial")
        with patch("urllib.request.urlopen", return_value=response):
            with self.assertRaises(update.UpdateError) as caught:
                update.fetch_latest()
        self.assertIn("could not download from GitHub", str(caught.exception))

    def test_invalid_json_is_a_user_facing_error(self):
        with patch.object(update, "_get", return_value=b"not json"):
            with self.assertRaises(update.UpdateError) as caught:
                update.fetch_latest()
        self.assertIn("invalid release metadata", str(caught.exception))

    def test_exact_release_uses_its_tag_endpoint(self):
        with (
            patch.object(update, "_get", return_value=_release_payload("1.2.0-beta.3")) as get,
            patch("platform.machine", return_value="x86_64"),
        ):
            release = update.fetch_release("v1.2.0-beta.3")

        self.assertEqual(release.version, "1.2.0-beta.3")
        self.assertTrue(get.call_args.args[0].endswith("/releases/tags/v1.2.0-beta.3"))

    def test_exact_release_rejects_unpublished_version_forms(self):
        """The rejection quotes the version as typed, tag prefix and all."""
        for version in ("1.2", "1.2.3-preview.1", "1.2.3+local", "../../latest", "vv1.2.3"):
            with self.subTest(version=version):
                with self.assertRaises(update.UpdateError) as caught:
                    update.fetch_release(version)
                self.assertEqual(str(caught.exception), f"invalid release version: {version}")

    def test_exact_release_metadata_must_match_the_requested_tag(self):
        with patch.object(update, "_get", return_value=_release_payload("1.2.4")):
            with self.assertRaises(update.UpdateError) as caught:
                update.fetch_release("1.2.3")
        self.assertIn("refusing to install", str(caught.exception))

    def _refusing_urlopen(self, code: int):
        """Patch urllib.request.urlopen to raise an HTTPError, closing its synthesised body."""
        error = urllib.error.HTTPError("url", code, "Not Found", None, None)
        self.addCleanup(error.close)
        return patch("urllib.request.urlopen", side_effect=error)

    def test_missing_exact_release_has_a_specific_404_error(self):
        with self._refusing_urlopen(404):
            with self.assertRaises(update.UpdateError) as caught:
                update.fetch_release("1.2.3")
        self.assertEqual(str(caught.exception), "release v1.2.3 was not found on GitHub")

    def test_missing_latest_release_reports_that_no_stable_release_exists(self):
        with self._refusing_urlopen(404):
            with self.assertRaises(update.UpdateError) as caught:
                update.fetch_latest()
        self.assertEqual(
            str(caught.exception),
            f"no stable releases were found for {update.PROJECT_REPO}",
        )

    def test_missing_tag_message_applies_to_any_release_lookup(self):
        with patch.object(update, "_get", return_value=json.dumps({"assets": []}).encode()):
            with self.assertRaises(update.UpdateError) as caught:
                update.fetch_release("1.2.3")
        self.assertEqual(str(caught.exception), "the release has no version tag")


class ChecksumTests(unittest.TestCase):
    def test_matching_digest_passes(self):
        with TemporaryDirectory() as tmp:
            archive = Path(tmp) / "a.tar.gz"
            archive.write_bytes(b"payload")
            digest = hashlib.sha256(b"payload").hexdigest()
            update._verify_checksum(archive, f"{digest}  a.tar.gz\n".encode())

    def test_mismatched_digest_raises(self):
        with TemporaryDirectory() as tmp:
            archive = Path(tmp) / "a.tar.gz"
            archive.write_bytes(b"payload")
            with self.assertRaises(update.UpdateError) as caught:
                update._verify_checksum(archive, b"%s  a.tar.gz\n" % (b"0" * 64))
            self.assertIn("nothing was installed", str(caught.exception))

    def test_empty_checksum_file_raises(self):
        with TemporaryDirectory() as tmp:
            archive = Path(tmp) / "a.tar.gz"
            archive.write_bytes(b"payload")
            with self.assertRaises(update.UpdateError):
                update._verify_checksum(archive, b"")

    def test_malformed_checksum_raises(self):
        with TemporaryDirectory() as tmp:
            archive = Path(tmp) / "a.tar.gz"
            archive.write_bytes(b"payload")
            with self.assertRaises(update.UpdateError) as caught:
                update._verify_checksum(archive, b"not-a-digest\n")
        self.assertIn("valid SHA-256", str(caught.exception))


class InstallerTests(unittest.TestCase):
    def test_preserves_existing_service_state(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "bin").mkdir()
            (root / "install.sh").touch()
            (root / "bin" / "theater-dimmer").touch()
            (root / "bin" / "theater-art").touch()
            with patch("subprocess.run", return_value=SimpleNamespace(returncode=0)) as run:
                update._run_installer(root)
        self.assertEqual(run.call_args.args[0][1], "--preserve-service")

    def test_missing_dimmer_is_rejected_before_install(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "bin").mkdir()
            (root / "install.sh").touch()
            (root / "bin" / "theater-art").touch()
            with self.assertRaises(update.UpdateError) as caught:
                update._run_installer(root)
        self.assertIn("theater-dimmer", str(caught.exception))

    def test_missing_art_is_rejected_before_install(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "bin").mkdir()
            (root / "install.sh").touch()
            (root / "bin" / "theater-dimmer").touch()
            with self.assertRaises(update.UpdateError) as caught:
                update._run_installer(root)
        self.assertIn("theater-art", str(caught.exception))


class ApplyGuardTests(unittest.TestCase):
    def _apply_explicit_release(
        self, version: str
    ) -> tuple[io.StringIO, MagicMock, MagicMock, MagicMock]:
        """Run apply() for a specific version with archive extraction and installer mocked."""
        archive = b"archive"
        checksum = f"{hashlib.sha256(archive).hexdigest()}  release.tar.gz\n".encode()
        stream = io.StringIO()
        root = MagicMock(spec=Path)
        root.is_dir.return_value = True
        with (
            patch.object(
                update,
                "fetch_release",
                return_value=update.Release(version, "archive", "checksum"),
            ) as fetch,
            patch.object(update, "_get", side_effect=[archive, checksum]),
            patch("tarfile.open"),
            patch.object(update, "_run_installer") as install,
            patch("pathlib.Path.iterdir", return_value=[root]),
        ):
            update.apply(version, stream=stream)
        return stream, fetch, install, root

    def test_no_op_when_already_current(self):
        stream = io.StringIO()
        with (
            patch.object(update, "__version__", "1.0.0"),
            patch.object(update, "fetch_latest", return_value=update.Release("1.0.0", "u", "c")),
        ):
            self.assertEqual(update.apply(stream=stream), 0)
        self.assertIn("up to date", stream.getvalue())

    def test_a_testing_build_is_told_how_to_return_to_stable(self):
        stream = io.StringIO()
        with (
            patch.object(update, "__version__", "1.1.0-beta.1"),
            patch.object(update, "fetch_latest", return_value=update.Release("1.0.0", "u", "c")),
        ):
            self.assertEqual(update.apply(stream=stream), 0)
        self.assertIn("is a testing build", stream.getvalue())
        self.assertIn("theater-mode update --release 1.0.0", stream.getvalue())

    def test_refuses_a_release_with_no_checksum(self):
        with patch.object(
            update,
            "fetch_latest",
            return_value=update.Release("999.0.0", "https://example.invalid/x", None),
        ):
            with self.assertRaises(update.UpdateError) as caught:
                update.apply(stream=io.StringIO())
        self.assertIn("unverified", str(caught.exception))
        self.assertIn("release v999.0.0", str(caught.exception))

    def test_explicit_release_can_downgrade(self):
        stream, fetch, install, root = self._apply_explicit_release("0.0.1")

        fetch.assert_called_once_with("0.0.1")
        install.assert_called_once_with(root)
        self.assertIn("Downgrading theater-mode", stream.getvalue())

    def test_explicit_current_release_is_reinstalled(self):
        stream, _, install, root = self._apply_explicit_release(update.__version__)

        install.assert_called_once_with(root)
        self.assertIn("Reinstalling theater-mode", stream.getvalue())

    def test_explicit_newer_release_uses_updating_verb(self):
        stream, _, _, _ = self._apply_explicit_release("999.0.0")

        self.assertIn("Updating theater-mode", stream.getvalue())

    def test_reports_when_this_architecture_has_no_build(self):
        with patch.object(
            update, "fetch_latest", return_value=update.Release("999.0.0", None, None)
        ):
            with self.assertRaises(update.UpdateError) as caught:
                update.apply(stream=io.StringIO())
        self.assertIn("--build", str(caught.exception))
        self.assertIn("release v999.0.0", str(caught.exception))

    def test_invalid_archive_is_a_user_facing_error(self):
        archive = b"not a tar archive"
        checksum = f"{hashlib.sha256(archive).hexdigest()}  release.tar.gz\n".encode()
        with (
            patch.object(
                update,
                "fetch_latest",
                return_value=update.Release("999.0.0", "archive", "checksum"),
            ),
            patch.object(update, "_get", side_effect=[archive, checksum]),
        ):
            with self.assertRaises(update.UpdateError) as caught:
                update.apply(stream=io.StringIO())
        self.assertIn("could not unpack", str(caught.exception))

    def test_success_describes_installed_files_without_claiming_live_activation(self):
        archive = b"archive"
        checksum = f"{hashlib.sha256(archive).hexdigest()}  release.tar.gz\n".encode()
        stream = io.StringIO()
        root = MagicMock(spec=Path)
        root.is_dir.return_value = True
        with (
            patch.object(
                update,
                "fetch_latest",
                return_value=update.Release("999.0.0", "archive", "checksum"),
            ),
            patch.object(update, "_get", side_effect=[archive, checksum]),
            patch("tarfile.open"),
            patch.object(update, "_run_installer"),
            patch("pathlib.Path.iterdir", return_value=[root]),
        ):
            update.apply(stream=stream)

        self.assertIn("999.0.0 files are installed", stream.getvalue())

    def test_the_update_banner_is_flushed_before_the_installer_runs(self):
        """The installer inherits this descriptor and writes to it directly.

        A buffered stream that has not been flushed emits the banner after the installer's
        own output, which misorders the account of what happened in any redirected log.
        """
        order: list[str] = []

        class RecordingStream(io.StringIO):
            def flush(self) -> None:
                order.append("flush")
                super().flush()

        archive = b"archive"
        checksum = f"{hashlib.sha256(archive).hexdigest()}  release.tar.gz\n".encode()
        stream = RecordingStream()
        root = MagicMock(spec=Path)
        root.is_dir.return_value = True
        with (
            patch.object(
                update,
                "fetch_latest",
                return_value=update.Release("999.0.0", "archive", "checksum"),
            ),
            patch.object(update, "_get", side_effect=[archive, checksum]),
            patch("tarfile.open"),
            patch.object(
                update, "_run_installer", side_effect=lambda root: order.append("install")
            ),
            patch("pathlib.Path.iterdir", return_value=[root]),
        ):
            update.apply(stream=stream)

        self.assertIn("Updating theater-mode", stream.getvalue())
        self.assertIn("flush", order)
        self.assertLess(order.index("flush"), order.index("install"))


if __name__ == "__main__":
    unittest.main()
