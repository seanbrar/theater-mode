"""Effects engine package providing log, brightness, wallpaper, and composite effects."""

from theater_mode.effects.base import Effect
from theater_mode.effects.brightness import BrightnessEffect
from theater_mode.effects.composite import CompositeEffect
from theater_mode.effects.log import LogEffect
from theater_mode.effects.wallpaper import WallpaperEffect

EFFECTS: dict[str, type[Effect]] = {
    "log": LogEffect,
    "brightness": BrightnessEffect,
    "wallpaper": WallpaperEffect,
}

__all__ = [
    "Effect",
    "LogEffect",
    "BrightnessEffect",
    "WallpaperEffect",
    "CompositeEffect",
    "EFFECTS",
]
