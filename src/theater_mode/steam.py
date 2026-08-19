"""Steam game detection, window matching, and library artwork processing."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from theater_mode.config import get_dev_config
from theater_mode.constants import (
    ART_CACHE,
    IGNORED_CLASSES,
    STEAM_APP_CLASS,
    STEAM_LAUNCH_ARG,
    STEAM_LIBRARY_CACHES,
)
from theater_mode.utils import read_process_cmdline, read_process_environ

log = logging.getLogger("theater-moded")

ARTWORK_CACHE_VERSION = 2
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


def _fast_feather_mask(width: int, height: int, feather: int, *, horizontal: bool = False) -> Any:
    """Generate a feather mask from a single gradient row or column."""
    from PIL import Image

    length = width if horizontal else height
    gradient = bytearray(length)
    denominator = max(1, feather - 1)
    for position in range(feather):
        gradient[position] = int(255 * position / denominator)
    gradient[feather : length - feather] = b"\xff" * (length - 2 * feather)
    for position in range(feather):
        gradient[length - feather + position] = int(255 * (feather - 1 - position) / denominator)

    strip_size = (length, 1) if horizontal else (1, length)
    strip = Image.frombytes("L", strip_size, bytes(gradient))
    return strip.resize((width, height), Image.Resampling.NEAREST)


def build_artwork(appid: str, width: int, height: int, dim_factor: float) -> Path | None:
    """Generate and cache a raw ARGB8888 composite from Steam hero art at target resolution.

    Overlays hero artwork onto a blurred, darkened ambient background with feathered
    edges, baking dim_factor directly into image brightness for the dimmer helper.
    """
    source = find_hero_art(appid)
    if source is None:
        return None

    dim_millis = round(max(0.0, min(1.0, dim_factor)) * 1000)
    brightness = 1.0 - dim_millis / 1000
    target = ART_CACHE / f"{appid}-v{ARTWORK_CACHE_VERSION}-{width}x{height}-d{dim_millis:04d}.argb"
    if target.exists() and target.stat().st_mtime >= source.stat().st_mtime:
        return target

    try:
        from PIL import Image, ImageEnhance, ImageFilter

        with Image.open(source) as artwork:
            artwork = artwork.convert("RGB")
            src_w, src_h = artwork.width, artwork.height

            # Backdrop: Determine source crop bounding box in source coordinates
            target_ar = width / height
            src_ar = src_w / src_h
            if src_ar > target_ar:
                crop_w = round(src_h * target_ar)
                crop_left = (src_w - crop_w) // 2
                crop_box = (crop_left, 0, crop_left + crop_w, src_h)
            else:
                crop_h = round(src_w / target_ar)
                crop_top = (src_h - crop_h) // 2
                crop_box = (0, crop_top, src_w, crop_top + crop_h)

            # Downscaled backdrop: Resize cropped source to 1/8 scale and blur
            downscale = 8
            low_w = max(1, width // downscale)
            low_h = max(1, height // downscale)
            backdrop_low = artwork.resize((low_w, low_h), Image.Resampling.BILINEAR, box=crop_box)
            blur_radius = max(2, (width // 60) // downscale)
            backdrop_low = backdrop_low.filter(ImageFilter.GaussianBlur(radius=blur_radius))
            backdrop_low = ImageEnhance.Brightness(backdrop_low).enhance(0.45 * brightness)

            # Upscale blurred backdrop to target dimensions
            backdrop = backdrop_low.resize((width, height), Image.Resampling.BILINEAR)
            del backdrop_low

            # Foreground: Contain the complete artwork and feather the exposed axis
            artwork_dimmed = ImageEnhance.Brightness(artwork).enhance(0.75 * brightness)
            fg_scale = min(width / src_w, height / src_h)
            fg_width = max(1, round(src_w * fg_scale))
            fg_height = max(1, round(src_h * fg_scale))
            foreground = artwork_dimmed.resize((fg_width, fg_height), Image.Resampling.LANCZOS)
            del artwork_dimmed

            mask = None
            if fg_width < width:
                feather = max(1, min(fg_width // 4, (width - fg_width) // 2 + fg_width // 8))
                mask = _fast_feather_mask(fg_width, fg_height, feather, horizontal=True)
            elif fg_height < height:
                feather = max(1, min(fg_height // 4, (height - fg_height) // 2 + fg_height // 8))
                mask = _fast_feather_mask(fg_width, fg_height, feather)

            position = ((width - fg_width) // 2, (height - fg_height) // 2)
            backdrop.paste(foreground, position, mask)
            del foreground, mask

            raw = backdrop.convert("RGBA").tobytes("raw", "BGRA")

            ART_CACHE.mkdir(parents=True, exist_ok=True)
            # Generation is synchronous, so one reusable sibling keeps crash debris bounded.
            tmp = target.with_suffix(".tmp")
            tmp.write_bytes(raw)
            tmp.replace(target)
    except Exception:
        log.exception("could not build artwork for appid %s", appid)
        return None

    log.debug("built artwork %s (%dx%d, brightness %.2f)", target, width, height, brightness)
    return target
