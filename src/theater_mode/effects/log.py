"""Dry-run logging effect that touches no hardware."""

from __future__ import annotations

import logging

from theater_mode.effects.base import Effect

log = logging.getLogger("theater-moded")


class LogEffect(Effect):
    """Dry run effect: logs detected events and proposed display changes without modifying state."""

    name = "log"

    def apply(self, game_output: str, other_outputs: list[str], appid: str) -> None:
        log.info(
            "DRY RUN: would apply theater mode for appid %s — game on %s, would affect %s",
            appid,
            game_output,
            ", ".join(other_outputs) or "(no other outputs)",
        )

    def revert(self, immediate: bool = False) -> None:
        log.info("DRY RUN: would revert theater mode")
