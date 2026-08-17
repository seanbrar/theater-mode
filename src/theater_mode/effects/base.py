"""Abstract base class for theater mode display effects."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class Effect(ABC):
    """Abstract base class for an effect applied to secondary displays."""

    name: str = "none"

    # Duration required for the effect to physically take effect on displays.
    # Non-zero durations indicate gradual transitions (e.g. hardware brightness ramp),
    # allowing composite pipelines to coordinate instant actions (like wallpaper swaps)
    # when the screens are darkest.
    transition_seconds: float = 0.0

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

    def saved_state(self) -> dict[str, Any] | None:
        """Return state snapshot required for crash recovery, or None if no changes applied."""
        return None

    def recover(self, saved: dict[str, Any]) -> None:
        """Restore display state left behind by a previous abnormal termination or crash."""
