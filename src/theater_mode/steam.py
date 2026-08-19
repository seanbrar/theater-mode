"""Steam game detection, window matching, and library artwork processing."""

from __future__ import annotations

import logging
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


def steam_appid_for_window(resource_class: str, pid: int) -> str | None:
    """Identify if a window belongs to a Steam game, returning its AppID if detected.

    Detection Order:
    1. Filter out known desktop shell/helper classes (e.g., plasmashell, steam client UI).
    2. Check WM_CLASS matching `steam_app_<appid>` (Proton and standard native games).
    3. Inspect process environment variables for `SteamGameId` or `SteamAppId`.
    4. Inspect process command line arguments for `AppId=<appid>` (covers Gamescope
       sessions where Gamescope launches prior to Steam environment injection).
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
    """Locate local cached Steam library hero artwork for a given AppID.

    Note: Local artwork is available for games whose store or library page has been
    viewed in the Steam client. Returns None if artwork is unavailable.
    """
    override = get_dev_config().force_art_dir
    library_caches = (override,) if override is not None else STEAM_LIBRARY_CACHES
    candidates = [
        candidate
        for library_cache in library_caches
        if (app_cache_dir := library_cache / appid).is_dir()
        for candidate in app_cache_dir.rglob("library_hero.jpg")
    ]
    if not candidates:
        return None

    # Pick the largest candidate file (resolving hash directories vs flat layouts)
    return max(candidates, key=lambda p: p.stat().st_size)


def artwork_render_size(width: int, height: int) -> tuple[int, int]:
    """Fit an output inside the artwork buffer limit without changing its aspect ratio."""
    scale = min(1.0, ARTWORK_MAX_WIDTH / width, ARTWORK_MAX_HEIGHT / height)
    return max(1, round(width * scale)), max(1, round(height * scale))


def find_art_binary() -> Path | None:
    """Locate the compiled theater-art executable."""
    return find_helper_binary(ART_BINARY_NAME, "THEATER_ART_BIN", "art")


def build_artwork(appid: str, width: int, height: int, dim_factor: float) -> Path | None:
    """Generate and cache a raw ARGB8888 composite from Steam hero art at target resolution.

    Overlays hero artwork onto a blurred, darkened ambient background with feathered
    edges, baking dim_factor directly into image brightness for the dimmer helper.
    """
    source = find_hero_art(appid)
    if source is None:
        return None

    dim_millis = round(max(0.0, min(1.0, dim_factor)) * 1000)
    target = ART_CACHE / f"{appid}-v{ARTWORK_CACHE_VERSION}-{width}x{height}-d{dim_millis:04d}.argb"
    if target.exists() and target.stat().st_mtime >= source.stat().st_mtime:
        return target

    binary = find_art_binary()
    if binary is None:
        log.warning("could not find %s helper to generate artwork", ART_BINARY_NAME)
        return None

    ART_CACHE.mkdir(parents=True, exist_ok=True)

    try:
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
    except subprocess.TimeoutExpired:
        log.error("theater-art timed out generating artwork for appid %s", appid)
        return None
    except Exception:
        log.exception("could not build artwork for appid %s", appid)
        return None

    log.debug("built artwork %s (%dx%d, dim %d/1000)", target, width, height, dim_millis)
    return target
