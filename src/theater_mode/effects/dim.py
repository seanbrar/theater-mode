"""Wayland layer-shell cinematic dimming and artwork effect."""

from __future__ import annotations

import contextlib
import logging
import os
import shutil
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
from theater_mode.steam import build_artwork

log = logging.getLogger("theater-moded")

# The helper speaks Wayland; the config speaks about windows. Translate at the boundary
# rather than leaking zwlr_layer_shell_v1 vocabulary into a user's config file.
PLACEMENT_LAYERS = {"over_windows": "overlay", "behind_windows": "bottom"}


def find_dimmer_binary() -> Path | None:
    """Locate the compiled theater-dimmer executable."""
    env_path = os.environ.get("THEATER_DIMMER_BIN")
    if env_path and Path(env_path).is_file() and os.access(env_path, os.X_OK):
        return Path(env_path)

    # Check sibling directory in repo/package
    pkg_bin = Path(__file__).parent.parent / "dimmer" / DIMMER_BINARY_NAME
    if pkg_bin.is_file() and os.access(pkg_bin, os.X_OK):
        return pkg_bin

    # Check user bin directory (~/.local/bin or $XDG_BIN_HOME)
    local_bin = (
        Path(os.environ.get("XDG_BIN_HOME", Path.home() / ".local/bin")) / DIMMER_BINARY_NAME
    )
    if local_bin.is_file() and os.access(local_bin, os.X_OK):
        return local_bin

    # Check PATH
    which_bin = shutil.which(DIMMER_BINARY_NAME)
    if which_bin:
        return Path(which_bin)

    return None


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

    @override
    def apply(self, game_output: str, other_outputs: list[str], appid: str) -> None:
        if not other_outputs:
            log.info("no secondary outputs to dim")
            self.revert()
            return

        targets = sorted(other_outputs)
        # Read connector EDID once so [outputs.<make:model:serial>] rules can match.
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
        # Read the connector modes once, not once per output.
        sizes = output_modes() if any(s.art for s in settings.values()) else {}

        with_art: list[str] = []
        for output in targets:
            resolved = settings[output]
            # Re-sent every time: a helper that has restarted comes back at its default.
            self._send(self.layer_command(output, resolved.placement))
            size = sizes.get(output) if resolved.art else None
            artwork = (
                build_artwork(appid, size[0], size[1], resolved.dim_factor)
                if appid and size
                else None
            )
            self._send(self.art_command(output, artwork, size))
            if artwork is not None:
                with_art.append(output)

        # One batched DIM selects the dimmed set: listed outputs animate to the global
        # values and every other output fades out. Outputs whose resolved settings differ
        # are then retuned individually, which leaves the rest of the set untouched.
        joined = ",".join(targets)
        batched = f"DIM {joined} {self._dim_factor:.3f} {self._duration:.2f} {self._curve}"
        if not self._send(batched):
            return
        self._dimmed = True

        globals_ = (self._dim_factor, self._duration, self._curve)
        for output in targets:
            resolved = settings[output]
            if (resolved.dim_factor, resolved.duration, resolved.curve) == globals_:
                continue
            self._send(
                f"DIM_OUTPUT {output} {resolved.dim_factor:.3f}"
                f" {resolved.duration:.2f} {resolved.curve}"
            )

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

    @override
    def revert(self, immediate: bool = False) -> None:
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
