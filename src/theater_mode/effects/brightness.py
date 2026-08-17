"""Hardware display brightness dimming effect with baseline tracking."""

from __future__ import annotations

import logging
from typing import Any

from theater_mode.display.kscreen import output_brightness, set_output_brightness
from theater_mode.effects.base import Effect

log = logging.getLogger("theater-moded")


class BrightnessEffect(Effect):
    """Dims secondary displays via hardware brightness controls during gameplay.

    Architecture & Hardware Timing Notes:
    - Single-Write Target: DDC/CI commands are sent once per state transition. External
      monitors communicate over I2C and process commands through their internal scaler/MCU.
      Sending a single target percentage allows the monitor's internal hardware ramp to execute
      smoothly, avoiding the I2C queue latency, dropped frames, and stutter caused by software loops.
    - Baseline Capture: Original brightness levels are captured upon first application and
      retained until full revert. This prevents window recreation during game startup from
      progressively ratcheting brightness lower.
    - Settle Duration: transition_seconds represents the physical delay (DDC dispatch + panel ramp)
      used by CompositeEffect to schedule abrupt changes (e.g. wallpaper swap) when dark.
    """

    name = "brightness"

    def __init__(self, dim_factor: float, settle_seconds: float = 1.5) -> None:
        self._dim_factor = dim_factor
        self.transition_seconds = settle_seconds
        self._baseline: dict[str, int] = {}

    def apply(self, game_output: str, other_outputs: list[str], appid: str) -> None:
        current_levels = output_brightness()
        targets: dict[str, int] = {}

        for output in other_outputs:
            if output not in self._baseline:
                current = current_levels.get(output)
                if current is None:
                    log.info("output %s has no brightness control; leaving it alone", output)
                    continue
                self._baseline[output] = int(round(current * 100))

            targets[output] = max(1, int(self._baseline[output] * self._dim_factor))

        if not targets:
            log.warning("theater mode active but nothing could be dimmed")
            return

        # If an output has a baseline but is no longer in other_outputs (i.e. the game moved to it),
        # restore it to baseline in the same batched write.
        for output, base in self._baseline.items():
            targets.setdefault(output, base)

        log.info(
            "dimming %s",
            ", ".join(f"{o} {self._baseline[o]}%->{t}%" for o, t in sorted(targets.items())),
        )
        set_output_brightness(targets)

    def revert(self, immediate: bool = False) -> None:
        if not self._baseline:
            return

        targets = dict(self._baseline)
        self._baseline.clear()
        log.info("restoring %s", ", ".join(f"{o} -> {v}%" for o, v in sorted(targets.items())))
        set_output_brightness(targets)

    def saved_state(self) -> dict[str, Any] | None:
        return {"brightness_percent": dict(self._baseline)} if self._baseline else None

    def recover(self, saved: dict[str, Any]) -> None:
        values = saved.get("brightness_percent") or {}
        if not values:
            return
        log.warning("restoring brightness left behind by a previous run: %s", values)
        set_output_brightness({output: int(value) for output, value in values.items()})
