"""Abstract base class and construction options for theater mode display effects."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

from theater_mode.config.schema import DEFAULT_CURVE, DEFAULT_DIM_FACTOR, DEFAULT_DURATION

if TYPE_CHECKING:
    from theater_mode.config import ResolvedConfig


@dataclass(frozen=True, slots=True)
class EffectOptions:
    """Configuration options passed to effect initializers."""

    dim_factor: float = DEFAULT_DIM_FACTOR
    dim_duration: float = DEFAULT_DURATION
    dim_curve: str = DEFAULT_CURVE
    art: bool = True
    resolved_config: ResolvedConfig | None = None

    @classmethod
    def from_config(cls, config: ResolvedConfig) -> EffectOptions:
        """Create options directly from a ResolvedConfig object."""
        return cls(
            dim_factor=config.effect.dim_factor,
            dim_duration=config.transition.duration,
            dim_curve=config.transition.curve,
            art=config.effect.art,
            resolved_config=config,
        )


class Effect(ABC):
    """Abstract base class for secondary display effects."""

    name: str = "none"

    @classmethod
    def create(cls, options: EffectOptions) -> Effect:
        """Instantiate an effect from configuration options."""
        return cls()

    def update_options(self, options: EffectOptions) -> None:
        """Adopt new options while the effect is live (no-op for stateless effects)."""

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
