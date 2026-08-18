"""Daemon core, state machine, and window tracking."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

import gi

gi.require_version("GLib", "2.0")
from gi.repository import GLib  # noqa: E402

from theater_mode.display.drm import connected_outputs
from theater_mode.effects.base import Effect
from theater_mode.steam import steam_appid_for_window
from theater_mode.utils import parse_bool, parse_int

log = logging.getLogger("theater-moded")


@dataclass
class TrackedWindow:
    """Represents a window tracked by the compositor script."""

    window_id: str
    resource_class: str
    pid: int
    output: str
    fullscreen: bool
    appid: str | None = None
    normal: bool = True

    @property
    def is_game(self) -> bool:
        return self.appid is not None


@dataclass
class Daemon:
    """Core state machine managing window lifecycle and display effects."""

    effect: Effect
    require_fullscreen: bool = False
    revert_delay: float = 3.0
    stage_delay: float = 1.5
    windows: dict[str, TrackedWindow] = field(default_factory=dict)
    active_output: str | None = None
    _applied_others: list[str] | None = None
    _snapshot: set[str] | None = None
    _pending_revert: int | None = None
    _pending_stage: int | None = None

    # -- State Machine & Lifecycle -----------------------------------------

    def game_windows(self) -> list[TrackedWindow]:
        """Return all active windows identified as games, filtering by fullscreen if configured."""
        games = [w for w in self.windows.values() if w.is_game]
        if self.require_fullscreen:
            games = [w for w in games if w.fullscreen]
        return games

    def reconcile(self) -> None:
        """Synchronize active display effects with the current set of tracked game windows."""
        games = self.game_windows()

        if not games:
            self._cancel_pending_stage()
            self.effect.cancel_pending()
            if self.active_output is not None and self._pending_revert is None:
                if self.revert_delay > 0:
                    log.info("no game windows left; reverting in %.1fs", self.revert_delay)
                    self._pending_revert = GLib.timeout_add(
                        int(self.revert_delay * 1000), self._revert_now
                    )
                else:
                    self._revert_now()
            return

        # Game window detected: cancel any pending revert from launcher transitions
        self._cancel_pending_revert()

        stage = self._stage(games)
        if stage is None:
            return
        if stage.output == self.active_output:
            self._cancel_pending_stage()
            self._follow_output_changes(stage)
            return

        if self.active_output is None:
            # Initial activation: apply immediately without staging delay
            self._commit_stage(stage.output, stage.appid or "", stage.resource_class)
            return

        # Output migration: require the game to persist on the new screen before retargeting
        if self._pending_stage is not None:
            return
        log.info(
            "game appears to have moved from %s to %s; confirming for %.1fs",
            self.active_output,
            stage.output,
            self.stage_delay,
        )
        self._pending_stage = GLib.timeout_add(int(self.stage_delay * 1000), self._confirm_stage)

    def _follow_output_changes(self, stage: TrackedWindow) -> None:
        """Re-apply effect if display topology changed while game is running."""
        others = self._other_outputs(stage.output)
        if others == self._applied_others:
            return

        log.info(
            "display set changed while a game was running (%s -> %s); re-applying",
            ", ".join(self._applied_others or []) or "none",
            ", ".join(others) or "none",
        )
        self._commit_stage(stage.output, stage.appid or "", stage.resource_class)

    def _other_outputs(self, game_output: str) -> list[str]:
        return sorted({o for o in self.all_outputs() if o != game_output})

    def _stage(self, games: list[TrackedWindow]) -> TrackedWindow | None:
        """Determine primary game window defining active display focus (fullscreen preferred)."""
        if not games:
            return None
        return next((w for w in games if w.fullscreen), games[0])

    def _confirm_stage(self) -> bool:
        """Confirm stage transition after stability timeout."""
        self._pending_stage = None
        stage = self._stage(self.game_windows())
        if stage is None or stage.output == self.active_output:
            log.info("game did not stay on new screen; leaving theater mode where it is")
            return GLib.SOURCE_REMOVE

        log.info("game moved from %s to %s; re-applying", self.active_output, stage.output)
        self.effect.revert()
        self._commit_stage(stage.output, stage.appid or "", stage.resource_class)
        return GLib.SOURCE_REMOVE

    def _commit_stage(self, output: str, appid: str, resource_class: str) -> None:
        """Commit effect application to target output."""
        others = self._other_outputs(output)
        log.info(
            "game detected: appid=%s class=%s on %s (other outputs: %s)",
            appid,
            resource_class,
            output,
            ", ".join(others) or "none",
        )
        self.effect.apply(output, others, appid)
        self.active_output = output
        self._applied_others = others

    def _cancel_pending_stage(self) -> None:
        if self._pending_stage is not None:
            GLib.source_remove(self._pending_stage)
            self._pending_stage = None

    def _revert_now(self, immediate: bool = False) -> bool:
        """Execute immediate effect reversion."""
        self._pending_revert = None
        if self.active_output is None:
            return GLib.SOURCE_REMOVE
        log.info("reverting")
        self.effect.revert(immediate=immediate)
        self.active_output = None
        self._applied_others = None
        return GLib.SOURCE_REMOVE

    def _cancel_pending_revert(self) -> None:
        if self._pending_revert is None:
            return
        GLib.source_remove(self._pending_revert)
        self._pending_revert = None
        log.info("a game window returned within %.1fs; staying in theater mode", self.revert_delay)

    def all_outputs(self) -> set[str]:
        """Aggregate known outputs from active window metadata and DRM sysfs."""
        outputs = {w.output for w in self.windows.values() if w.output}
        outputs.update(connected_outputs())
        return outputs

    # -- D-Bus Interface Handlers ------------------------------------------

    def window_opened(
        self,
        window_id: str,
        resource_class: str,
        pid: str | int,
        output: str,
        fullscreen: str | bool,
        normal: str | bool = "true",
    ) -> None:
        parsed_pid = parse_int(pid)
        is_fullscreen = parse_bool(fullscreen)
        is_normal = parse_bool(normal)

        known = self.windows.get(window_id)
        if known is not None and (known.pid, known.resource_class) == (parsed_pid, resource_class):
            appid = known.appid
        else:
            appid = steam_appid_for_window(resource_class, parsed_pid)

        window = TrackedWindow(
            window_id=window_id,
            resource_class=resource_class,
            pid=parsed_pid,
            output=output,
            fullscreen=is_fullscreen,
            appid=appid,
            normal=is_normal,
        )

        self.windows[window_id] = window
        if self._snapshot is not None:
            self._snapshot.add(window_id)

        if appid and (known is None or not known.is_game):
            log.info(
                "game window opened: appid=%s class=%s pid=%d on %s",
                appid,
                resource_class,
                parsed_pid,
                output,
            )
        else:
            log.debug(
                "window opened: class=%s pid=%d on %s normal=%s fullscreen=%s (not a game)",
                resource_class,
                parsed_pid,
                output,
                is_normal,
                is_fullscreen,
            )
        self.reconcile()

    def window_changed(self, window_id: str, output: str, fullscreen: str | bool) -> None:
        window = self.windows.get(window_id)
        if window is None:
            log.debug("change for untracked window %s; ignoring", window_id)
            return

        window.output = output
        window.fullscreen = parse_bool(fullscreen)
        if window.is_game:
            log.info(
                "game window moved: appid=%s now on %s fullscreen=%s",
                window.appid,
                output,
                fullscreen,
            )
        self.reconcile()

    def window_closed(self, window_id: str) -> None:
        window = self.windows.pop(window_id, None)
        if window and window.is_game:
            log.info("game window closed: appid=%s", window.appid)
        self.reconcile()

    def snapshot_begin(self) -> None:
        self._snapshot = set()

    def snapshot_end(self) -> None:
        if self._snapshot is None:
            return
        stale = set(self.windows) - self._snapshot
        for window_id in stale:
            window = self.windows.pop(window_id)
            log.info("dropping stale window from snapshot: class=%s", window.resource_class)
        self._snapshot = None
        if stale:
            self.reconcile()

    def status(self) -> str:
        """Return JSON summary of current daemon state and tracked windows."""
        return json.dumps(
            {
                "effect": self.effect.name,
                "active_output": self.active_output,
                "revert_pending": self._pending_revert is not None,
                "revert_delay": self.revert_delay,
                "require_fullscreen": self.require_fullscreen,
                "tracked_windows": len(self.windows),
                "games": [
                    {
                        "appid": w.appid,
                        "class": w.resource_class,
                        "pid": w.pid,
                        "output": w.output,
                        "fullscreen": w.fullscreen,
                    }
                    for w in self.windows.values()
                    if w.is_game
                ],
                "outputs": sorted(self.all_outputs()),
            },
            indent=2,
        )

    def simulate(self, appid: str, output: str) -> str:
        """Simulate game launch for manual validation without starting a game."""
        fake_id = f"simulated-{appid}"
        self.windows[fake_id] = TrackedWindow(fake_id, f"steam_app_{appid}", 0, output, True, appid)
        self.reconcile()
        return f"simulated game {appid} on {output}; call Clear to undo"

    def clear(self, immediate: bool = False) -> str:
        """Clear all window tracking and force immediate effect reversion (escape hatch)."""
        self.windows.clear()
        self._cancel_pending_revert()
        self._cancel_pending_stage()
        self.effect.cancel_pending()
        self._revert_now(immediate=immediate)
        return "cleared"
