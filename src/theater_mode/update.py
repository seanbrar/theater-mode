"""Self-update against published GitHub releases for installs that no package manager owns."""

from __future__ import annotations

import hashlib
import json
import platform
import re
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from theater_mode import __version__
from theater_mode.constants import DIMMER_BINARY_NAME, PROJECT_REPO, RELEASE_API

_TIMEOUT = 30
_USER_AGENT = f"theater-mode/{__version__}"


class UpdateError(Exception):
    """An update could not be completed."""


@dataclass(frozen=True)
class Release:
    """A published release and the artifact matching this machine."""

    version: str
    tarball_url: str | None
    checksum_url: str | None


_VERSION_RE = re.compile(
    r"^v?(\d+)(?:\.(\d+))?(?:\.(\d+))?"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)


def _version_key(text: str) -> tuple[int, int, int, int, tuple[tuple[int, int | str], ...]] | None:
    """Parse the version forms used by release tags into SemVer ordering fields."""
    match = _VERSION_RE.fullmatch(text)
    if match is None:
        return None
    major, minor, patch = (int(part or 0) for part in match.group(1, 2, 3))
    prerelease = match.group(4)
    if prerelease is None:
        return major, minor, patch, 1, ()
    identifiers = tuple(
        (0, int(part)) if part.isdigit() else (1, part) for part in prerelease.split(".")
    )
    return major, minor, patch, 0, identifiers


def is_newer(candidate: str, current: str) -> bool:
    """Report whether candidate is a strictly later version than current."""
    candidate_key = _version_key(candidate)
    current_key = _version_key(current)
    return candidate_key is not None and current_key is not None and candidate_key > current_key


def _https_url(value: object) -> str | None:
    """Accept a download URL only if it is HTTPS, so _get never opens file:// or ftp://."""
    return value if isinstance(value, str) and value.startswith("https://") else None


def _get(url: str) -> bytes:
    if not url.startswith("https://"):
        raise UpdateError(f"refusing to fetch a non-HTTPS URL: {url}")
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:  # noqa: S310
            return response.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise UpdateError(
                f"no releases published for {PROJECT_REPO} yet, or the repository is private"
            ) from exc
        if exc.code in (403, 429):
            raise UpdateError(
                "GitHub is rate-limiting this address; try again in a few minutes"
            ) from exc
        raise UpdateError(f"could not reach GitHub ({exc.code} {exc.reason})") from exc
    except urllib.error.URLError as exc:
        raise UpdateError(f"could not reach GitHub: {exc.reason}") from exc
    except OSError as exc:
        raise UpdateError(f"could not download from GitHub: {exc}") from exc


def fetch_latest() -> Release:
    """Look up the newest published release and the asset built for this architecture."""
    try:
        payload = json.loads(_get(RELEASE_API))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise UpdateError("GitHub returned invalid release metadata; try again later") from exc
    if not isinstance(payload, dict):
        raise UpdateError("GitHub returned unexpected release metadata; try again later")
    version = str(payload.get("tag_name", "")).lstrip("v")
    if not version:
        raise UpdateError("the latest release has no version tag")

    filename = f"theater-mode-v{version}-linux-{platform.machine()}.tar.gz"
    tarball = checksum = None
    assets = payload.get("assets", [])
    if not isinstance(assets, list):
        raise UpdateError("GitHub returned unexpected release metadata; try again later")
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        name = asset.get("name", "")
        if name == filename:
            tarball = _https_url(asset.get("browser_download_url"))
        elif name == f"{filename}.sha256":
            checksum = _https_url(asset.get("browser_download_url"))
    return Release(version=version, tarball_url=tarball, checksum_url=checksum)


def _verify_checksum(archive: Path, expected_blob: bytes) -> None:
    """Compare the archive against the published sha256, which lists 'digest  filename'."""
    expected = expected_blob.decode("utf-8", "replace").split()
    if not expected:
        raise UpdateError("the published checksum file is empty")
    expected_digest = expected[0].lower()
    if len(expected_digest) != 64 or any(c not in "0123456789abcdef" for c in expected_digest):
        raise UpdateError("the published checksum is not a valid SHA-256 digest")
    try:
        with archive.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
    except OSError as exc:
        raise UpdateError(f"could not verify the downloaded release: {exc}") from exc
    if digest != expected_digest:
        raise UpdateError(
            "downloaded archive does not match its published checksum.\n"
            f"  expected {expected_digest}\n"
            f"  actual   {digest}\n"
            "  The download was corrupted or tampered with; nothing was installed."
        )


def _run_installer(root: Path) -> None:
    """Hand off to the installer inside the freshly downloaded tree."""
    installer = root / "install.sh"
    dimmer = root / "bin" / DIMMER_BINARY_NAME
    if not installer.is_file():
        raise UpdateError("the downloaded release contains no install.sh")
    if not dimmer.is_file():
        raise UpdateError("the downloaded release contains no theater-dimmer")
    try:
        installer.chmod(0o755)
        dimmer.chmod(0o755)
        result = subprocess.run(  # noqa: S603
            [str(installer), "--preserve-service"], cwd=root, check=False
        )
    except OSError as exc:
        raise UpdateError(f"could not run the installer: {exc}") from exc
    if result.returncode != 0:
        raise UpdateError(f"the installer exited with status {result.returncode}")


def check(stream=sys.stdout) -> int:
    """Report whether a newer release exists, without changing anything."""
    release = fetch_latest()
    if is_newer(release.version, __version__):
        print(f"theater-mode {release.version} is available (you have {__version__}).", file=stream)
        print("Install it with: theater-mode update", file=stream)
    else:
        print(f"theater-mode {__version__} is up to date.", file=stream)
    return 0


def apply(stream=sys.stdout) -> int:
    """Download, verify, and install the newest release over this one."""
    release = fetch_latest()
    if not is_newer(release.version, __version__):
        print(f"theater-mode {__version__} is already up to date.", file=stream)
        return 0

    if not release.tarball_url:
        raise UpdateError(
            f"release {release.version} has no build for {platform.machine()}.\n"
            "  Install from source instead: ./install.sh --build"
        )
    if not release.checksum_url:
        raise UpdateError(
            f"release {release.version} publishes no checksum for this architecture; "
            "refusing to install an unverified archive"
        )

    print(f"Updating theater-mode {__version__} -> {release.version}", file=stream)

    with tempfile.TemporaryDirectory(prefix="theater-mode-update-") as tmp:
        work = Path(tmp)
        archive = work / "release.tar.gz"
        try:
            archive.write_bytes(_get(release.tarball_url))
        except OSError as exc:
            raise UpdateError(f"could not save the downloaded release: {exc}") from exc

        _verify_checksum(archive, _get(release.checksum_url))

        extracted = work / "tree"
        try:
            with tarfile.open(archive) as tar:
                tar.extractall(extracted, filter="data")
        except (OSError, tarfile.TarError) as exc:
            raise UpdateError(f"could not unpack the downloaded release: {exc}") from exc

        roots = [child for child in extracted.iterdir() if child.is_dir()]
        if len(roots) != 1:
            raise UpdateError("the downloaded release has an unexpected layout")
        _run_installer(roots[0])

    print(f"theater-mode is now at {release.version}.", file=stream)
    return 0
