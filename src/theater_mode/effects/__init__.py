"""Effects engine package providing the dry-run log effect and the Wayland dimmer."""

from theater_mode.effects.base import Effect, EffectOptions
from theater_mode.effects.dim import DimEffect
from theater_mode.effects.log import LogEffect

EFFECTS: dict[str, type[Effect]] = {
    "log": LogEffect,
    "dim": DimEffect,
}

__all__ = [
    "EFFECTS",
    "DimEffect",
    "Effect",
    "EffectOptions",
    "LogEffect",
]
