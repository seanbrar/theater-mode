"""Effects engine package providing the Wayland dimmer."""

from theater_mode.effects.base import Effect, EffectOptions
from theater_mode.effects.dim import DimEffect

__all__ = [
    "DimEffect",
    "Effect",
    "EffectOptions",
]
