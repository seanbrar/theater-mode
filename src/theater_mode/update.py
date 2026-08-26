"""Self-update against published GitHub releases for installs that no package manager owns."""

from __future__ import annotations

import hashlib
import http.client
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
from theater_mode.constants import (
    ART_BINARY_NAME,
    DIMMER_BINARY_NAME,
    PROJECT_REPO,
    RELEASE_API,
    RELEASES_API,
)

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
    r"^v?([0-9]+)(?:\.([0-9]+))?(?:\.([0-9]+))?"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
_PUBLISHED_VERSION_RE = re.compile(
    r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:-(?:alpha|beta|rc|exp)\.(?:0|[1-9][0-9]*))?$"
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


def is_prerelease(version: str) -> bool:
    """Report whether a version carries a prerelease identifier."""
    match = _VERSION_RE.fullmatch(version)
    return match is not None and match.group(4) is not None


def _https_url(value: object) -> str | None:
    """Accept a download URL only if it is HTTPS, so _get never opens file:// or ftp://."""
    return value if isinstance(value, str) and value.startswith("https://") else None


def _get(url: str, *, not_found: str) -> bytes:
    """Return an HTTPS response body, reporting a 404 as not_found."""
    if not url.startswith("https://"):
        raise UpdateError(f"refusing to fetch a non-HTTPS URL: {url}")
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:  # noqa: S310
            return response.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise UpdateError(not_found) from exc
        if exc.code in (403, 429):
            raise UpdateError(
                "GitHub is rate-limiting this address; try again in a few minutes"
            ) from exc
        raise UpdateError(f"could not reach GitHub ({exc.code} {exc.reason})") from exc
    except urllib.error.URLError as exc:
        raise UpdateError(f"could not reach GitHub: {exc.reason}") from exc
    except (http.client.HTTPException, OSError) as exc:
        # A truncated or malformed response raises HTTPException, which is not an OSError.
        raise UpdateError(f"could not download from GitHub: {exc}") from exc


def release_asset_name(version: str) -> str:
    """Name the release archive built for this machine, as bin/make-release publishes it."""
    return f"theater-mode-v{version}-linux-{platform.machine()}.tar.gz"


def normalize_release_version(text: str) -> str:
    """Return a supported release version without its optional tag prefix.

    Raise UpdateError when the version is outside the project's published tag forms.
    """
    version = text.removeprefix("v")
    if _PUBLISHED_VERSION_RE.fullmatch(version) is None:
        raise UpdateError(f"invalid release version: {text}")
    return version


def fetch_release(version: str) -> Release:
    """Look up one published release and the asset built for this architecture."""
    requested = normalize_release_version(version)
    return _fetch_release(
        f"{RELEASES_API}/tags/v{requested}",
        expected_version=requested,
        not_found=f"release v{requested} was not found on GitHub",
    )


def fetch_latest() -> Release:
    """Look up the newest stable release and the asset built for this architecture."""
    return _fetch_release(
        RELEASE_API,
        not_found=f"no stable releases were found for {PROJECT_REPO}",
    )


def _fetch_release(url: str, *, not_found: str, expected_version: str | None = None) -> Release:
    """Read release metadata from url and select this machine's artifact."""
    try:
        payload = json.loads(_get(url, not_found=not_found))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise UpdateError("GitHub returned invalid release metadata; try again later") from exc
    if not isinstance(payload, dict):
        raise UpdateError("GitHub returned unexpected release metadata; try again later")
    tag = payload.get("tag_name")
    version = tag.removeprefix("v") if isinstance(tag, str) else ""
    if not version:
        raise UpdateError("the release has no version tag")
    if expected_version is not None and version != expected_version:
        raise UpdateError(
            f"GitHub returned v{version} when v{expected_version} was requested; "
            "refusing to install it"
        )

    filename = release_asset_name(version)
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
    art = root / "bin" / ART_BINARY_NAME
    if not installer.is_file():
        raise UpdateError("the downloaded release contains no install.sh")
    if not dimmer.is_file():
        raise UpdateError("the downloaded release contains no theater-dimmer")
    if not art.is_file():
        raise UpdateError("the downloaded release contains no theater-art")
    try:
        installer.chmod(0o755)
        dimmer.chmod(0o755)
        art.chmod(0o755)
        result = subprocess.run(  # noqa: S603
            [str(installer), "--preserve-service"], cwd=root, check=False
        )
    except OSError as exc:
        raise UpdateError(f"could not run the installer: {exc}") from exc
    if result.returncode != 0:
        raise UpdateError(f"the installer exited with status {result.returncode}")


def _verb(candidate: str) -> str:
    """Return the display verb for an installation step."""
    if is_newer(candidate, __version__):
        return "Updating"
    if is_newer(__version__, candidate):
        return "Downgrading"
    return "Reinstalling"


def _report_current(stable_version: str, stream) -> None:
    """Print that theater-mode is up to date, or how a testing build can return to stable."""
    if is_prerelease(__version__):
        print(f"theater-mode {__version__} is a testing build.", file=stream)
        print(
            f"The latest stable release is {stable_version}. "
            f"Return to it with: theater-mode update --release {stable_version}",
            file=stream,
        )
    else:
        print(f"theater-mode {__version__} is up to date.", file=stream)


def check(*, stream=sys.stdout) -> int:
    """Report whether a newer stable release exists, without changing anything."""
    release = fetch_latest()
    if is_newer(release.version, __version__):
        print(f"theater-mode {release.version} is available (you have {__version__}).", file=stream)
        print("Install it with: theater-mode update", file=stream)
    else:
        _report_current(release.version, stream)
    return 0


def apply(release_version: str | None = None, *, stream=sys.stdout) -> int:
    """Download, verify, and install the stable or explicitly selected release."""
    release = fetch_latest() if release_version is None else fetch_release(release_version)
    if release_version is None and not is_newer(release.version, __version__):
        _report_current(release.version, stream)
        return 0

    machine = platform.machine()
    if not release.tarball_url:
        raise UpdateError(
            f"release v{release.version} has no build for {machine}.\n"
            "  Install from source instead:\n"
            f"    git clone https://github.com/{PROJECT_REPO}\n"
            "    cd theater-mode && ./install.sh --build"
        )
    if not release.checksum_url:
        raise UpdateError(
            f"release v{release.version} publishes no checksum for this architecture; "
            "refusing to install an unverified archive"
        )

    # The installer writes straight to this descriptor. A block-buffered stream, which is
    # what stdout is whenever it is not a terminal, would otherwise flush this line after it.
    print(
        f"{_verb(release.version)} theater-mode {__version__} -> {release.version}",
        file=stream,
        flush=True,
    )

    with tempfile.TemporaryDirectory(prefix="theater-mode-update-") as tmp:
        work = Path(tmp)
        archive = work / "release.tar.gz"
        try:
            archive.write_bytes(
                _get(
                    release.tarball_url,
                    not_found=f"release v{release.version} has no archive for {machine} on GitHub",
                )
            )
        except OSError as exc:
            raise UpdateError(f"could not save the downloaded release: {exc}") from exc

        _verify_checksum(
            archive,
            _get(
                release.checksum_url,
                not_found=f"release v{release.version} has no checksum for {machine} on GitHub",
            ),
        )

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

    print(f"theater-mode {release.version} files are installed.", file=stream)
    return 0
