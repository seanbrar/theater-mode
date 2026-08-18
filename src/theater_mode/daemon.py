"""Daemon core, state machine, and window tracking."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from theater_mode.config import (
    DevConfig,
    Diagnostic,
    ResolvedConfig,
    commit_user_config,
    load_resolved_config,
    validate_updates,
)
from theater_mode.display.drm import connected_outputs, output_identities
from theater_mode.effects.base import Effect, EffectOptions
from theater_mode.steam import steam_appid_for_window
from theater_mode.utils import parse_bool, parse_int

log = logging.getLogger("theater-moded")


class TimerScheduler(Protocol):
    """Abstraction for scheduling and cancelling timer callbacks."""

    def timeout_add(self, delay_ms: int, callback: Callable[[], None]) -> Any:
        """Schedule a callback after delay_ms. Returns a cancellation token."""
        ...

    def source_remove(self, tag: Any) -> None:
        """Cancel a pending timer callback."""
        ...


class GLibTimerScheduler:
    """Production timer scheduler using GLib's event loop."""

    def timeout_add(self, delay_ms: int, callback: Callable[[], None]) -> Any:
        import gi

        gi.require_version("GLib", "2.0")
        from gi.repository import GLib

        def _wrapper() -> bool:
            callback()
            return False

        return GLib.timeout_add(delay_ms, _wrapper)

    def source_remove(self, tag: Any) -> None:
        import gi

        gi.require_version("GLib", "2.0")
        from gi.repository import GLib

        GLib.source_remove(tag)


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


class Daemon:
    """Core state machine managing window lifecycle and display effects."""

    def __init__(
        self,
        effect: Effect,
        config: ResolvedConfig | None = None,
        diagnostics: list[Diagnostic] | None = None,
        dev_config: DevConfig | None = None,
        on_config_changed: Callable[[], None] | None = None,
        scheduler: TimerScheduler | None = None,
    ) -> None:
        self.effect = effect
        self.config = config or ResolvedConfig()
        self.diagnostics = diagnostics or []
        self.dev_config = dev_config
        self.on_config_changed = on_config_changed
        self.scheduler: TimerScheduler = scheduler or GLibTimerScheduler()
        self.windows: dict[str, TrackedWindow] = {}
        self.active_output: str | None = None
        self._applied_others: list[str] | None = None
        self._snapshot: set[str] | None = None
        self._pending_revert: Any | None = None
        self._pending_stage: Any | None = None
        self._session_overrides: dict[str, Any] = {}

        # Sync effect options on startup
        self.effect.update_options(EffectOptions.from_config(self.config))

    @property
    def require_fullscreen(self) -> bool:
        return self.config.daemon.require_fullscreen

    @property
    def revert_delay(self) -> float:
        return self.config.daemon.revert_delay

    @property
    def stage_delay(self) -> float:
        return self.config.daemon.stage_delay

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
                    self._pending_revert = self.scheduler.timeout_add(
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
        self._pending_stage = self.scheduler.timeout_add(
            int(self.stage_delay * 1000), self._confirm_stage
        )

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

    def _confirm_stage(self) -> None:
        """Confirm stage transition after stability timeout."""
        self._pending_stage = None
        stage = self._stage(self.game_windows())
        if stage is None or stage.output == self.active_output:
            log.info("game did not stay on new screen; leaving theater mode where it is")
            return

        log.info("game moved from %s to %s; re-applying", self.active_output, stage.output)
        self.effect.revert()
        self._commit_stage(stage.output, stage.appid or "", stage.resource_class)

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
            self.scheduler.source_remove(self._pending_stage)
            self._pending_stage = None

    def _revert_now(self, immediate: bool = False) -> None:
        """Execute immediate effect reversion."""
        self._pending_revert = None
        if self.active_output is None:
            return
        log.info("reverting")
        self.effect.revert(immediate=immediate)
        self.active_output = None
        self._applied_others = None

    def _cancel_pending_revert(self) -> None:
        if self._pending_revert is None:
            return
        self.scheduler.source_remove(self._pending_revert)
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

    # -- Configuration API Handlers ----------------------------------------

    def get_outputs(self) -> str:
        """Return each connected output with the config keys that would address it."""
        identities = output_identities()
        return json.dumps(
            [
                {
                    "connector": name,
                    "vendor": identity.vendor,
                    "pnp_id": identity.pnp_id,
                    "model": identity.model,
                    "serial": identity.serial,
                    "match_keys": list(identity.match_keys),
                    "active": name == self.active_output,
                }
                for name, identity in sorted(identities.items())
            ],
            indent=2,
        )

    def get_resolved(self) -> str:
        """Return JSON dump of full resolved configuration with provenance."""
        return json.dumps(self.config.to_dict(), indent=2)

    def get_diagnostics(self) -> str:
        """Return JSON list of all configuration diagnostics and warnings."""
        return json.dumps([d.to_dict() for d in self.diagnostics], indent=2)

    @staticmethod
    def _parse_updates(keys_json: str, action: str) -> dict[str, Any] | str:
        """Decode a {key path: value} payload, or return an error string for the caller."""
        try:
            updates = json.loads(keys_json)
        except json.JSONDecodeError as e:
            return f"error: invalid JSON payload: {e}"
        if not isinstance(updates, dict):
            return f"error: {action} payload must be a JSON object mapping keys to values"
        return updates

    def preview(self, keys_json: str) -> str:
        """Apply in-memory session overrides without writing to disk."""
        updates = self._parse_updates(keys_json, "preview")
        if isinstance(updates, str):
            return updates

        accepted, rejected = validate_updates(updates)
        self._session_overrides.update(accepted)
        self._reload_internal()
        log.info("applied session preview for %d keys", len(accepted))
        return self._report(f"preview applied for {len(accepted)} keys", rejected)

    def revert_preview(self) -> str:
        """Discard in-memory session overrides and revert to resolved disk configuration."""
        count = len(self._session_overrides)
        self._session_overrides.clear()
        self._reload_internal()
        log.info("reverted session preview (%d keys cleared)", count)
        return f"preview reverted ({count} keys cleared)"

    def commit(self, keys_json: str) -> str:
        """Persist key updates to the user configuration file atomically and reload."""
        updates = self._parse_updates(keys_json, "commit")
        if isinstance(updates, str):
            return updates

        # Never write a key or value that the loader would refuse to read back.
        accepted, rejected = validate_updates(updates)
        if not accepted:
            return self._report("error: nothing to commit", rejected)

        user_path = self.dev_config.user_config_override if self.dev_config else None
        ok, msg = commit_user_config(accepted, user_config_path=user_path)
        if not ok:
            log.error("commit failed: %s", msg)
            return f"error: {msg}"

        log.info("committed %d keys to user configuration file", len(accepted))
        self._reload_internal()
        return self._report(f"committed {len(accepted)} keys successfully", rejected)

    @staticmethod
    def _report(summary: str, rejected: list[Diagnostic]) -> str:
        """Append rejected-key detail to a result summary."""
        if not rejected:
            return summary
        for diagnostic in rejected:
            log.warning("rejected %s: %s", diagnostic.key_path, diagnostic.message)
        return summary + "; rejected: " + "; ".join(d.message for d in rejected)

    def reload(self) -> str:
        """Re-read configuration files from disk and refresh active effects."""
        self._reload_internal()
        log.info("reloaded configuration from disk")
        return "configuration reloaded"

    def _reload_internal(self) -> None:
        """Re-resolve configuration and update active effect without restarting daemon."""
        new_config, new_diagnostics = load_resolved_config(
            session_overrides=self._session_overrides,
            dev_config=self.dev_config,
        )
        self.config = new_config
        self.diagnostics = new_diagnostics

        # Update running effect with new options
        self.effect.update_options(EffectOptions.from_config(self.config))

        # The effect class is chosen once at startup; a mode change needs a restart.
        if self.config.effect.mode != self.effect.name:
            log.warning(
                "effect.mode is now '%s' but '%s' is running; restart theater-mode.service"
                " to switch effects",
                self.config.effect.mode,
                self.effect.name,
            )

        # Re-apply effect if a game is currently active
        if self.active_output is not None and self._applied_others is not None:
            stage = self._stage(self.game_windows())
            if stage is not None:
                self.effect.apply(stage.output, self._applied_others, stage.appid or "")

        if self.on_config_changed:
            try:
                self.on_config_changed()
            except Exception:
                log.exception("error invoking on_config_changed callback")
