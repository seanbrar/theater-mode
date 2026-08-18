"""Abstract base class and construction options for theater mode display effects."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from theater_mode.constants import (
    DEFAULT_DIM_CURVE,
    DEFAULT_DIM_DURATION,
    DEFAULT_DIM_FACTOR,
)


@dataclass(frozen=True)
class EffectOptions:
    """Configuration options passed to effect initializers."""

    dim_factor: float = DEFAULT_DIM_FACTOR
    dim_duration: float = DEFAULT_DIM_DURATION
    dim_curve: str = DEFAULT_DIM_CURVE
    art: bool = True


class Effect(ABC):
    """Abstract base class for secondary display effects."""

    name: str = "none"

    @classmethod
    def create(cls, options: EffectOptions) -> Effect:
        """Instantiate an effect from configuration options."""
        return cls()

    @abstractmethod
    def apply(self, game_output: str, other_outputs: list[str], appid: str) -> None:
        """Apply the effect to secondary displays when a game is active on game_output."""
        raise NotImplementedError

    @abstractmethod
    def revert(self, immediate: bool = False) -> None:
        """Revert displays to their original state."""
        raise NotImplementedError

    def cancel_pending(self) -> None:
        """Cancel any queued asynchronous or timed transitions without reverting active state."""
