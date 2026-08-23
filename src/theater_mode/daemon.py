"""Daemon core, state machine, and window tracking."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from theater_mode.config import (
    DevConfig,
    Diagnostic,
    ResolvedConfig,
    commit_user_config,
    load_resolved_config,
    lookup_spec,
    split_key_path,
    unset_user_config,
    validate_updates,
)
from theater_mode.display.drm import connected_outputs, output_identities
from theater_mode.display.edid import OutputIdentity
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

    def __init__(self) -> None:
        import gi

        gi.require_version("GLib", "2.0")
        from gi.repository import GLib

        self._glib = GLib

    def timeout_add(self, delay_ms: int, callback: Callable[[], None]) -> Any:
        def _wrapper() -> bool:
            callback()
            return False

        return self._glib.timeout_add(delay_ms, _wrapper)

    def source_remove(self, tag: Any) -> None:
        self._glib.source_remove(tag)


@dataclass
class TrackedWindow:
    """Represents a window tracked by the compositor script."""

    window_id: str
    resource_class: str
    pid: int
    output: str
    fullscreen: bool
    appid: str | None = None

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
        self._compositor_outputs: set[str] = set()
        # Silence starts at daemon startup, before the detector's first snapshot.
        self._detector_contact: float = time.monotonic()
        self._snapshot: set[str] | None = None
        self._pending_revert: Any | None = None
        self._pending_stage: Any | None = None
        self._session_overrides: dict[str, Any] = {}

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

        self._cancel_pending_revert()

        stage = self._stage(games)
        if stage is None:
            return
        if stage.output == self.active_output:
            if self._applied_others and not self.effect.is_running:
                log.info("display effect helper exited unexpectedly; re-applying")
                self._commit_stage(stage.output, stage.appid or "", stage.resource_class)
                return
            self._cancel_pending_stage()
            self._follow_output_changes(stage)
            return

        if self.active_output is None:
            # stage_delay applies only when an active game moves to another output.
            self._commit_stage(stage.output, stage.appid or "", stage.resource_class)
            return

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
        """Return all secondary outputs given the active game output, sorted."""
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

    def _commit_stage(self, output: str, appid: str, resource_class: str) -> bool:
        """Commit effect application to the target output, returning whether dispatch succeeded."""
        others = self._other_outputs(output)
        log.info(
            "game detected: appid=%s class=%s on %s (other outputs: %s)",
            appid,
            resource_class,
            output,
            ", ".join(others) or "none",
        )
        if not self.effect.apply(output, others, appid):
            log.warning(
                "failed to apply display effect on %s; will retry on next reconcile", output
            )
            self.active_output = None
            self._applied_others = None
            return False

        self.active_output = output
        self._applied_others = others
        return True

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
        """Aggregate known outputs, preferring compositor reports over DRM sysfs fallback."""
        outputs = {w.output for w in self.windows.values() if w.output}
        outputs.update(self._compositor_outputs or connected_outputs())
        return outputs

    def window_opened(
        self,
        window_id: str,
        resource_class: str,
        pid: str | int,
        output: str,
        fullscreen: str | bool,
    ) -> None:
        """Handle window open event from KWin detector script."""
        parsed_pid = parse_int(pid)
        is_fullscreen = parse_bool(fullscreen)

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
                "window opened: class=%s pid=%d on %s fullscreen=%s (not a game)",
                resource_class,
                parsed_pid,
                output,
                is_fullscreen,
            )
        # Defer reconciliation during a snapshot until snapshot_end runs.
        if self._snapshot is None:
            self.reconcile()

    def window_changed(self, window_id: str, output: str, fullscreen: str | bool) -> None:
        """Handle window output or fullscreen geometry change from KWin detector script."""
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
        if self._snapshot is None:
            self.reconcile()

    def window_closed(self, window_id: str) -> None:
        """Handle window close event from KWin detector script."""
        window = self.windows.pop(window_id, None)
        if window and window.is_game:
            log.info("game window closed: appid=%s", window.appid)
        if self._snapshot is None:
            self.reconcile()

    def snapshot_begin(self, screens: str) -> None:
        """Begin full window pass from KWin detector script, tracking active window IDs."""
        # An empty list means the detector could not read its screens; keep the sysfs fallback.
        if screens:
            self._compositor_outputs = {s.strip() for s in screens.split(",") if s.strip()}
        self._snapshot = set()

    def snapshot_end(self) -> None:
        """Complete full window pass from KWin detector script and drop untracked stale windows."""
        if self._snapshot is None:
            return
        stale = set(self.windows) - self._snapshot
        for window_id in stale:
            window = self.windows.pop(window_id)
            log.info("dropping stale window from snapshot: class=%s", window.resource_class)
        self._snapshot = None
        self._detector_contact = time.monotonic()
        self.reconcile()

    def status(self) -> str:
        """Return JSON summary of current daemon state and tracked windows."""
        return json.dumps(
            {
                "effect": self.effect.name,
                "effect_process_running": bool(self.effect.is_running),
                "affected_outputs": list(self._applied_others or []),
                "detector_silence_seconds": round(time.monotonic() - self._detector_contact, 1),
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
        return f"simulated game {appid} on {output}; run 'theater-mode clear' to undo"

    def clear(self, immediate: bool = False) -> str:
        """Clear all window tracking and begin display restoration immediately (escape hatch)."""
        self.windows.clear()
        self._cancel_pending_revert()
        self._cancel_pending_stage()
        self.effect.cancel_pending()
        self._revert_now(immediate=immediate)
        return "cleared"

    def get_outputs(self) -> str:
        """Return JSON array of connected outputs and their configuration match keys."""
        identities = output_identities()
        # List every connector as well, so a display switched off in the compositor
        # still shows the match keys needed to write an [outputs.*] rule for it.
        results = []
        for name in sorted(set(identities) | self.all_outputs()):
            identity = identities.get(name) or OutputIdentity(connector=name)
            results.append(
                {
                    "connector": name,
                    "vendor": identity.vendor,
                    "pnp_id": identity.pnp_id,
                    "model": identity.model,
                    "serial": identity.serial,
                    "match_keys": list(identity.match_keys),
                    "active": name == self.active_output,
                }
            )
        return json.dumps(results, indent=2)

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

    def unset(self, keys_json: str) -> str:
        """Remove keys from the user configuration file so they fall back to a lower layer."""
        try:
            requested = json.loads(keys_json)
        except json.JSONDecodeError as e:
            return f"error: invalid JSON payload: {e}"
        if not isinstance(requested, list) or not all(isinstance(k, str) for k in requested):
            return "error: unset payload must be a JSON array of key paths"

        # Refuse keys the schema does not define, so a typo is reported rather than
        # silently succeeding as "already unset".
        known: set[str] = set()
        rejected: list[Diagnostic] = []
        for key_path in requested:
            split = split_key_path(key_path)
            if split is None or lookup_spec(key_path) is None:
                rejected.append(
                    Diagnostic(
                        key_path=key_path,
                        message=f"Unknown configuration key '{key_path}'",
                        severity="warning",
                    )
                )
            else:
                table, leaf = split
                known.add(f"{table}.{leaf}")

        if not known:
            return self._report("error: nothing to unset", rejected)

        user_path = self.dev_config.user_config_override if self.dev_config else None
        ok, msg, removed = unset_user_config(known, user_config_path=user_path)
        if not ok:
            log.error("unset failed: %s", msg)
            return f"error: {msg}"

        if removed:
            log.info("unset %d keys from user configuration file", len(removed))
            self._reload_internal()

        summary = f"unset {len(removed)} keys"
        if untouched := sorted(known - removed):
            summary += "; already unset: " + ", ".join(untouched)
        return self._report(summary, rejected)

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

        self.effect.update_options(EffectOptions.from_config(self.config))

        if self.active_output is not None and self._applied_others is not None:
            stage = self._stage(self.game_windows())
            if stage is not None:
                self.effect.apply(stage.output, self._applied_others, stage.appid or "")

        if self.on_config_changed:
            try:
                self.on_config_changed()
            except Exception:
                log.exception("error invoking on_config_changed callback")
