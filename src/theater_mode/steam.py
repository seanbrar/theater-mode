"""Steam game detection, window matching, and library artwork processing."""

from __future__ import annotations

import logging
from pathlib import Path

from theater_mode.constants import (
    ART_CACHE,
    IGNORED_CLASSES,
    STEAM_APP_CLASS,
    STEAM_LAUNCH_ARG,
    STEAM_LIBRARY_CACHE,
)
from theater_mode.utils import read_process_cmdline, read_process_environ

log = logging.getLogger("theater-moded")


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
    app_cache_dir = STEAM_LIBRARY_CACHE / appid
    if not app_cache_dir.is_dir():
        return None

    candidates = list(app_cache_dir.glob("**/library_hero.jpg"))
    if not candidates:
        return None

    # Pick the largest candidate file (resolving hash directories vs flat layouts)
    return max(candidates, key=lambda p: p.stat().st_size)


def build_wallpaper(appid: str, width: int, height: int) -> Path | None:
    """Generate and cache a composite wallpaper from Steam hero art at target resolution.

    The 1920x620 hero artwork is overlaid on a blurred, darkened ambient background
    with feathered horizontal seams to match the monitor's native aspect ratio.
    """
    source = find_hero_art(appid)
    if source is None:
        return None

    target = ART_CACHE / f"{appid}-{width}x{height}.jpg"
    if target.exists() and target.stat().st_mtime >= source.stat().st_mtime:
        return target

    try:
        from PIL import Image, ImageEnhance, ImageFilter

        with Image.open(source) as hero:
            hero = hero.convert("RGB")

            # Backdrop: Fill screen dimensions, apply gaussian blur and ambient darkening
            scale = max(width / hero.width, height / hero.height)
            backdrop = hero.resize(
                (max(1, round(hero.width * scale)), max(1, round(hero.height * scale))),
                Image.LANCZOS,
            )
            left = (backdrop.width - width) // 2
            top = (backdrop.height - height) // 2
            backdrop = backdrop.crop((left, top, left + width, top + height))
            backdrop = backdrop.filter(ImageFilter.GaussianBlur(radius=max(8, width // 60)))
            backdrop = ImageEnhance.Brightness(backdrop).enhance(0.45)

            # Foreground: Centered hero art with feathered edge gradient mask
            fg_height = max(1, round(hero.height * (width / hero.width)))
            foreground = hero.resize((width, fg_height), Image.LANCZOS)
            foreground = ImageEnhance.Brightness(foreground).enhance(0.75)

            feather = max(1, min(fg_height // 4, (height - fg_height) // 2 + fg_height // 8))
            mask = Image.new("L", (width, fg_height), 255)
            fade = Image.linear_gradient("L").resize((width, feather))
            mask.paste(fade.transpose(Image.FLIP_TOP_BOTTOM).point(lambda v: 255 - v), (0, 0))
            mask.paste(fade.point(lambda v: 255 - v), (0, fg_height - feather))

            backdrop.paste(foreground, (0, (height - fg_height) // 2), mask)

            ART_CACHE.mkdir(parents=True, exist_ok=True)
            backdrop.save(target, "JPEG", quality=90)
    except Exception:
        log.exception("could not build wallpaper for appid %s", appid)
        return None

    log.debug("built wallpaper %s", target)
    return target
