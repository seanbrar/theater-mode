"""Steam game detection, window matching, and library artwork processing."""

from __future__ import annotations

import logging
import stat
import subprocess
from pathlib import Path

from theater_mode.config import get_dev_config
from theater_mode.constants import (
    ART_BINARY_NAME,
    ART_CACHE,
    IGNORED_CLASSES,
    STEAM_APP_CLASS,
    STEAM_LAUNCH_ARG,
    STEAM_LIBRARY_CACHES,
)
from theater_mode.utils import find_helper_binary, read_process_cmdline, read_process_environ

log = logging.getLogger("theater-moded")

ARTWORK_CACHE_VERSION = 3
ARTWORK_MAX_WIDTH = 1920
ARTWORK_MAX_HEIGHT = 1080
ARTWORK_CACHE_MAX_BYTES = 128 * 1024 * 1024
ARTWORK_CACHE_TRIM_BYTES = 64 * 1024 * 1024


def steam_appid_for_window(resource_class: str, pid: int) -> str | None:
    """Return a Steam AppID identified from the window class or process, if any.

    Ignored window classes take precedence, followed by `steam_app_<appid>`, the
    `SteamGameId` and `SteamAppId` environment keys, and an `AppId` command-line argument.
    """
    if resource_class in IGNORED_CLASSES:
        return None

    match = STEAM_APP_CLASS.match(resource_class)
    if match:
        return match.group(1)

    environ = read_process_environ(pid)
    for key in ("SteamGameId", "SteamAppId"):
        value = environ.get(key, "")
        if value.isdigit() and int(value) > 0:
            return value

    match = STEAM_LAUNCH_ARG.search(read_process_cmdline(pid))
    if match and int(match.group(1)) > 0:
        return match.group(1)

    return None


def find_hero_art(appid: str) -> Path | None:
    """Return the largest cached Steam library hero for an AppID, or None if unavailable."""
    try:
        override = get_dev_config().force_art_dir
        library_caches = (override,) if override is not None else STEAM_LIBRARY_CACHES
        candidates: list[Path] = []
        for library_cache in library_caches:
            app_cache_dir = library_cache / appid
            if not app_cache_dir.is_dir():
                continue
            candidates.extend(app_cache_dir.rglob("library_hero.jpg"))

        if not candidates:
            return None

        return max(candidates, key=lambda p: p.stat().st_size)
    except OSError:
        return None


def artwork_render_size(width: int, height: int) -> tuple[int, int]:
    """Fit an output inside the artwork buffer limit without changing its aspect ratio."""
    scale = min(1.0, ARTWORK_MAX_WIDTH / width, ARTWORK_MAX_HEIGHT / height)
    return max(1, round(width * scale)), max(1, round(height * scale))


def find_art_binary() -> Path | None:
    """Locate the compiled theater-art executable."""
    return find_helper_binary(ART_BINARY_NAME, "THEATER_ART_BIN", "art")


def prune_artwork_cache(
    target_cache: Path = ART_CACHE,
    current_target: Path | None = None,
    max_bytes: int = ARTWORK_CACHE_MAX_BYTES,
    trim_to_bytes: int = ARTWORK_CACHE_TRIM_BYTES,
) -> None:
    """Trim an oversized artwork cache by age without removing current_target.

    Ignore filesystem errors and leave non-regular entries untouched.
    """
    try:
        if not target_cache.is_dir():
            return

        entries: list[tuple[Path, int, float]] = []
        total_size = 0
        for p in target_cache.glob("*.argb"):
            try:
                st = p.stat()
            except OSError:
                continue
            if not stat.S_ISREG(st.st_mode):
                continue
            entries.append((p, st.st_size, st.st_mtime))
            total_size += st.st_size

        if total_size <= max_bytes:
            return

        entries.sort(key=lambda item: item[2])
        for p, size, _ in entries:
            if p == current_target:
                continue
            p.unlink(missing_ok=True)
            total_size -= size
            if total_size <= trim_to_bytes:
                break
    except OSError:
        pass


def build_artwork(appid: str, width: int, height: int, dimming: float) -> Path | None:
    """Return a cached raw ARGB8888 composite at the requested resolution.

    The image has dimming baked into its brightness. Return None when source artwork or
    the helper is unavailable, rendering times out, or an I/O operation fails.
    """
    try:
        source = find_hero_art(appid)
        if source is None:
            return None

        dim_millis = round(max(0.0, min(1.0, dimming)) * 1000)
        target = (
            ART_CACHE / f"{appid}-v{ARTWORK_CACHE_VERSION}-{width}x{height}-d{dim_millis:04d}.argb"
        )
        if target.exists() and target.stat().st_mtime >= source.stat().st_mtime:
            try:
                target.touch()
            except OSError:
                pass
            prune_artwork_cache(ART_CACHE, current_target=target)
            return target

        binary = find_art_binary()
        if binary is None:
            log.warning("could not find %s helper to generate artwork", ART_BINARY_NAME)
            return None

        ART_CACHE.mkdir(parents=True, exist_ok=True)

        cmd = [
            str(binary),
            str(source),
            str(target),
            str(width),
            str(height),
            str(dim_millis),
        ]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=3.0,
            check=False,
        )
        if result.returncode != 0:
            log.error(
                "theater-art exited with code %d: %s", result.returncode, result.stderr.strip()
            )
            return None
        if not target.is_file():
            log.error("theater-art did not produce target file %s", target)
            return None

        prune_artwork_cache(ART_CACHE, current_target=target)
        log.debug("built artwork %s (%dx%d, dim %d/1000)", target, width, height, dim_millis)
        return target
    except subprocess.TimeoutExpired:
        log.error("theater-art timed out generating artwork for appid %s", appid)
        return None
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("could not build artwork for appid %s: %s", appid, exc)
        return None
