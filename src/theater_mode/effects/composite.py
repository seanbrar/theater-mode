"""Composite effect orchestrator for coordinating multi-effect pipelines."""

from __future__ import annotations

import logging
from typing import Any

import gi

gi.require_version("GLib", "2.0")
from gi.repository import GLib  # noqa: E402

from theater_mode.effects.base import Effect

log = logging.getLogger("theater-moded")


class CompositeEffect(Effect):
    """Combines and sequences multiple effects into an orchestrated pipeline.

    Transition Sequencing:
    - Application Order: Gradual effects (such as hardware dimming) are initiated first.
      Instant effects (such as wallpaper plugin switches) are delayed until the dimming
      settle period has elapsed so the wallpaper switch occurs while displays are dark.
    - Reversion Order: Abrupt effects are reverted first while displays are still dark,
      followed by gradual effects bringing luminance back up.
    """

    # Delay multiplier for triggering instant effects relative to gradual transition duration
    _INSTANT_AT = 1.0

    def __init__(self, effects: list[Effect]) -> None:
        self._effects = effects
        self.name = "+".join(e.name for e in effects)
        self._pending: int | None = None

    def apply(self, game_output: str, other_outputs: list[str], appid: str) -> None:
        self._cancel_pending()
        gradual = [e for e in self._effects if e.transition_seconds > 0]
        instant = [e for e in self._effects if e.transition_seconds <= 0]

        for effect in gradual:
            self._run(effect.apply, effect, "apply", game_output, other_outputs, appid)

        if not instant:
            return

        def run_instant() -> bool:
            self._pending = None
            for effect in instant:
                self._run(effect.apply, effect, "apply", game_output, other_outputs, appid)
            return GLib.SOURCE_REMOVE

        delay = max((e.transition_seconds for e in gradual), default=0.0) * self._INSTANT_AT
        if delay <= 0:
            run_instant()
        else:
            self._pending = GLib.timeout_add(int(delay * 1000), run_instant)

    def revert(self, immediate: bool = False) -> None:
        self._cancel_pending()
        # Restore abrupt effects first while monitors remain dimmed
        for effect in self._effects:
            if effect.transition_seconds <= 0:
                self._run(effect.revert, effect, "revert", immediate=immediate)
        # Restore luminance afterward
        for effect in self._effects:
            if effect.transition_seconds > 0:
                self._run(effect.revert, effect, "revert", immediate=immediate)

    def cancel_pending(self) -> None:
        if self._pending is not None:
            GLib.source_remove(self._pending)
            self._pending = None

    _cancel_pending = cancel_pending

    @staticmethod
    def _run(call, effect: Effect, action_name: str, *args, **kwargs) -> None:
        try:
            call(*args, **kwargs)
        except Exception:
            log.exception("effect %s failed during %s", effect.name, action_name)

    def saved_state(self) -> dict[str, Any] | None:
        state: dict[str, Any] = {}
        for effect in self._effects:
            part = effect.saved_state()
            if part:
                state[effect.name] = part
        return state or None

    def recover(self, saved: dict[str, Any]) -> None:
        for effect in self._effects:
            part = saved.get(effect.name)
            if part:
                effect.recover(part)
