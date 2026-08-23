"""Wayland layer-shell cinematic dimming and artwork effect."""

from __future__ import annotations

import contextlib
import logging
import subprocess
from pathlib import Path
from typing import override

from theater_mode.config import ResolvedConfig, ResolvedDisplaySettings, format_table_header
from theater_mode.config.schema import (
    DEFAULT_CURVE,
    DEFAULT_DIM_FACTOR,
    DEFAULT_DURATION,
    DEFAULT_PLACEMENT,
)
from theater_mode.constants import DIMMER_BINARY_NAME
from theater_mode.display.drm import output_identities, output_modes
from theater_mode.display.edid import OutputIdentity
from theater_mode.effects.base import Effect, EffectOptions
from theater_mode.steam import artwork_render_size, build_artwork
from theater_mode.utils import find_helper_binary

log = logging.getLogger("theater-moded")

PLACEMENT_LAYERS = {"over_windows": "overlay", "behind_windows": "bottom"}


def find_dimmer_binary() -> Path | None:
    """Locate the compiled theater-dimmer executable."""
    return find_helper_binary(DIMMER_BINARY_NAME, "THEATER_DIMMER_BIN", "dimmer")


class DimEffect(Effect):
    """Dims secondary displays via Wayland layer-shell overlays and optional game artwork."""

    name = "dim"

    def __init__(
        self,
        placement: str = DEFAULT_PLACEMENT,
        dim_factor: float = DEFAULT_DIM_FACTOR,
        duration: float = DEFAULT_DURATION,
        curve: str = DEFAULT_CURVE,
        art: bool = True,
        binary_path: Path | str | None = None,
        resolved_config: ResolvedConfig | None = None,
    ) -> None:
        self._placement = placement.lower()
        self._dim_factor = dim_factor
        self._duration = duration
        self._curve = curve.lower()
        self._art = art
        self._custom_binary = Path(binary_path) if binary_path else None
        self._resolved_config = resolved_config
        self._process: subprocess.Popen[str] | None = None
        self._dimmed = False

    @classmethod
    @override
    def create(cls, options: EffectOptions) -> DimEffect:
        return cls(
            placement=options.placement,
            dim_factor=options.dim_factor,
            duration=options.dim_duration,
            curve=options.dim_curve,
            art=options.art,
            resolved_config=options.resolved_config,
        )

    @override
    def update_options(self, options: EffectOptions) -> None:
        """Update active effect parameters dynamically."""
        self._placement = options.placement.lower()
        self._dim_factor = options.dim_factor
        self._duration = options.dim_duration
        self._curve = options.dim_curve.lower()
        self._art = options.art
        self._resolved_config = options.resolved_config

    def _ensure_process(self) -> bool:
        """Ensure the theater-dimmer helper subprocess is running."""
        if self._process is not None and self._process.poll() is None:
            return True

        binary = self._custom_binary or find_dimmer_binary()
        if not binary:
            log.error(
                "theater-dimmer binary not found. Run install.sh or build src/theater_mode/dimmer."
            )
            return False

        try:
            self._process = subprocess.Popen(
                [str(binary)],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                text=True,
                bufsize=1,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            log.error("failed to start theater-dimmer helper %s: %s", binary, exc)
            self._process = None
            return False

        log.debug("started theater-dimmer helper %s (pid %d)", binary, self._process.pid)
        return True

    def _running(self) -> bool:
        """Report whether the helper subprocess is currently active."""
        return self._process is not None and self._process.poll() is None

    def _close_process(self) -> None:
        """Close the helper process stdin and release the handle."""
        if self._process is None:
            return
        if self._process.stdin:
            with contextlib.suppress(OSError):
                self._process.stdin.close()
        self._process.poll()
        self._process = None

    def _send(self, cmd: str, *, start: bool = True) -> bool:
        """Write a command to the dimmer helper process."""
        if not self._running():
            self._close_process()
            if not start or not self._ensure_process():
                return False

        process = self._process
        if process is None or process.stdin is None:
            return False

        try:
            process.stdin.write(f"{cmd}\n")
            process.stdin.flush()
            return True
        except (BrokenPipeError, OSError) as exc:
            self._close_process()
            log.error("failed writing to dimmer helper: %s", exc)
            return False

    def _settings_for(
        self, output: str, identities: dict[str, OutputIdentity]
    ) -> ResolvedDisplaySettings:
        """Resolve this output's settings, falling back to the flat constructor values."""
        if self._resolved_config is None:
            return ResolvedDisplaySettings(
                output_id=output,
                placement=self._placement,
                dim_factor=self._dim_factor,
                art=self._art,
                duration=self._duration,
                curve=self._curve,
            )

        identity = identities.get(output)
        return self._resolved_config.resolve_for_output(
            output, identity.match_keys if identity else ()
        )

    @staticmethod
    def layer_command(output: str, placement: str) -> str:
        """Format the LAYER protocol command for an output."""
        return f"LAYER {output} {PLACEMENT_LAYERS.get(placement, 'overlay')}"

    @staticmethod
    def art_command(output: str, artwork: Path | None, size: tuple[int, int] | None) -> str:
        """Format the ART protocol command for an output."""
        if artwork is None or size is None:
            return f"ART {output}"
        return f"ART {output} {size[0]} {size[1]} {artwork}"

    def _log_output_rules(
        self, targets: list[str], settings: dict[str, ResolvedDisplaySettings]
    ) -> None:
        """Report the outputs a per-output rule actually changed, and what changed."""
        globals_ = {
            "placement": self._placement,
            "dim_factor": self._dim_factor,
            "duration": self._duration,
            "curve": self._curve,
            "art": self._art,
        }

        for output in targets:
            resolved = settings[output]
            if resolved.matched_key is None:
                continue

            deltas = [
                f"{name}={getattr(resolved, name)}"
                for name, value in globals_.items()
                if getattr(resolved, name) != value
            ]
            log.info(
                "%s matched %s%s",
                output,
                format_table_header(f"outputs.{resolved.matched_key}"),
                " -> " + ", ".join(deltas) if deltas else " (no change from global settings)",
            )

    @property
    @override
    def is_running(self) -> bool:
        """Report whether the dimmer helper process is currently alive."""
        return self._running()

    @override
    def apply(self, game_output: str, other_outputs: list[str], appid: str) -> bool:
        """Dispatch effect state to the dimmer helper.

        True means the helper accepted every command through its pipe, not that the
        compositor acknowledged the resulting Wayland state.
        """
        if not other_outputs:
            log.info("no secondary outputs to dim")
            self.revert()
            return True

        targets = sorted(other_outputs)
        identities = output_identities() if self._resolved_config is not None else {}
        settings = {output: self._settings_for(output, identities) for output in targets}

        # Identities carry display serial numbers, so keep the full dump at debug level.
        if identities and log.isEnabledFor(logging.DEBUG):
            log.debug(
                "output identities: %s",
                "; ".join(
                    f"{name}={' | '.join(identity.match_keys) or 'no EDID'}"
                    for name, identity in sorted(identities.items())
                ),
            )
        sizes = output_modes() if any(s.art for s in settings.values()) else {}

        with_art: list[str] = []
        artwork_requests: dict[tuple[str, int, int, float], Path | None] = {}
        for output in targets:
            resolved = settings[output]
            # A restarted helper has forgotten every output's layer.
            if not self._send(self.layer_command(output, resolved.placement)):
                return False
            output_size = sizes.get(output) if resolved.art else None
            render_size = artwork_render_size(*output_size) if output_size else None
            artwork = None
            if appid and render_size:
                request = (appid, *render_size, resolved.dim_factor)
                if request not in artwork_requests:
                    artwork_requests[request] = build_artwork(
                        appid, *render_size, resolved.dim_factor
                    )
                artwork = artwork_requests[request]
            if not self._send(self.art_command(output, artwork, render_size)):
                return False
            if artwork is not None:
                with_art.append(output)

        # One batched DIM selects the dimmed set: listed outputs animate to the global
        # values and every other output fades out. Outputs whose resolved settings differ
        # are then retuned individually, which leaves the rest of the set untouched.
        joined = ",".join(targets)
        batched = f"DIM {joined} {self._dim_factor:.3f} {self._duration:.2f} {self._curve}"
        if not self._send(batched):
            return False
        self._dimmed = True

        globals_ = (self._dim_factor, self._duration, self._curve)
        for output in targets:
            resolved = settings[output]
            if (resolved.dim_factor, resolved.duration, resolved.curve) == globals_:
                continue
            if not self._send(
                f"DIM_OUTPUT {output} {resolved.dim_factor:.3f}"
                f" {resolved.duration:.2f} {resolved.curve}"
            ):
                return False

        log.info(
            "cinematic dimming on [%s] (factor=%.2f, duration=%.1fs, curve=%s, placement=%s,"
            " artwork on: %s)",
            ", ".join(targets),
            self._dim_factor,
            self._duration,
            self._curve,
            self._placement,
            ", ".join(with_art) or "none",
        )
        self._log_output_rules(targets, settings)
        return True

    @override
    def revert(self, immediate: bool = False) -> None:
        """Fade out dimming overlays and restore secondary displays."""
        if not self._dimmed and not self._running():
            return

        duration = 0.001 if immediate else self._duration
        log.info("restoring displays (fade_out duration=%.1fs)", 0.0 if immediate else duration)
        self._send(f"FADE_OUT {duration:.3f} {self._curve}", start=False)
        self._dimmed = False

        if immediate:
            self.shutdown()

    def shutdown(self) -> None:
        """Cleanly terminate the dimmer helper process."""
        if self._process is None:
            return
        try:
            self._send("QUIT", start=False)
            self._process.wait(timeout=1.0)
        except (OSError, subprocess.SubprocessError):
            self._process.kill()
        finally:
            self._close_process()
