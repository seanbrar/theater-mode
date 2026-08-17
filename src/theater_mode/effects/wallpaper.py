"""Wallpaper effect that displays resolution-fitted Steam hero art on secondary screens."""

from __future__ import annotations

import logging
from typing import Any

from theater_mode.display.kscreen import output_sizes
from theater_mode.display.plasma import (
    output_desktop_map,
    read_wallpapers,
    restore_wallpapers,
    write_wallpapers,
)
from theater_mode.effects.base import Effect
from theater_mode.steam import build_wallpaper

log = logging.getLogger("theater-moded")


class WallpaperEffect(Effect):
    """Applies game-specific artwork to secondary screens during gameplay.

    Configuration Preservation:
    Only the active wallpaper plugin (`wallpaperPlugin`) is switched to `org.kde.image`
    while custom configurations remain in their respective containment groups. On revert,
    the original plugin identifier is restored, ensuring custom engines and playlists
    survive untouched.
    """

    name = "wallpaper"

    def __init__(self) -> None:
        # output -> (plugin_id, image_path) captured prior to modification
        self._saved: dict[str, tuple[str, str]] = {}

    def apply(self, game_output: str, other_outputs: list[str], appid: str) -> None:
        if not appid:
            return

        screens = output_desktop_map()
        sizes = output_sizes()
        changes: dict[int, str] = {}
        screens_to_read: list[int] = []

        for output in other_outputs:
            screen = screens.get(output)
            size = sizes.get(output)
            if screen is None or size is None:
                log.info("no Plasma screen found for %s; leaving wallpaper unchanged", output)
                continue

            wallpaper = build_wallpaper(appid, size[0], size[1])
            if wallpaper is None:
                log.info(
                    "no hero artwork available for appid %s; leaving wallpapers unchanged", appid
                )
                return

            if output not in self._saved:
                screens_to_read.append(screen)
            changes[screen] = str(wallpaper)

        if not changes:
            return

        if screens_to_read:
            current_configs = read_wallpapers(screens_to_read)
            for output in other_outputs:
                screen = screens.get(output)
                if screen is not None and screen in current_configs and output not in self._saved:
                    self._saved[output] = current_configs[screen]

        log.info("setting game wallpaper on %s", ", ".join(sorted(other_outputs)))
        write_wallpapers(changes)

    def revert(self, immediate: bool = False) -> None:
        if not self._saved:
            return

        screens = output_desktop_map()
        restore_map: dict[int, tuple[str, str]] = {}
        for output, saved in self._saved.items():
            screen = screens.get(output)
            if screen is not None:
                restore_map[screen] = saved

        log.info("restoring wallpapers on %s", ", ".join(sorted(self._saved)))
        self._saved.clear()
        if restore_map:
            restore_wallpapers(restore_map)

    def saved_state(self) -> dict[str, Any] | None:
        return {"wallpapers": {k: list(v) for k, v in self._saved.items()}} if self._saved else None

    def recover(self, saved: dict[str, Any]) -> None:
        values = saved.get("wallpapers") or {}
        if not values:
            return
        log.warning("restoring wallpapers left behind by a previous run: %s", list(values))
        self._saved = {k: (v[0], v[1]) for k, v in values.items()}
        self.revert(immediate=True)
