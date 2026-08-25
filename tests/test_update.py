"""Tests for release discovery, version ordering, and checksum enforcement."""

from __future__ import annotations

import hashlib
import io
import json
import unittest
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
            update._get("file:///etc/passwd")
        self.assertIn("non-HTTPS", str(caught.exception))

    def test_missing_tag_is_an_error(self):
        with patch.object(update, "_get", return_value=json.dumps({"assets": []}).encode()):
            with self.assertRaises(update.UpdateError):
                update.fetch_latest()

    def test_invalid_json_is_a_user_facing_error(self):
        with patch.object(update, "_get", return_value=b"not json"):
            with self.assertRaises(update.UpdateError) as caught:
                update.fetch_latest()
        self.assertIn("invalid release metadata", str(caught.exception))


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
    def test_no_op_when_already_current(self):
        stream = io.StringIO()
        with patch.object(
            update, "fetch_latest", return_value=update.Release(update.__version__, "u", "c")
        ):
            self.assertEqual(update.apply(stream=stream), 0)
        self.assertIn("up to date", stream.getvalue())

    def test_refuses_a_release_with_no_checksum(self):
        with patch.object(
            update,
            "fetch_latest",
            return_value=update.Release("999.0.0", "https://example.invalid/x", None),
        ):
            with self.assertRaises(update.UpdateError) as caught:
                update.apply(stream=io.StringIO())
        self.assertIn("unverified", str(caught.exception))

    def test_reports_when_this_architecture_has_no_build(self):
        with patch.object(
            update, "fetch_latest", return_value=update.Release("999.0.0", None, None)
        ):
            with self.assertRaises(update.UpdateError) as caught:
                update.apply(stream=io.StringIO())
        self.assertIn("--build", str(caught.exception))

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
