"""Wayland layer-shell cinematic dimming and artwork effect."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

from theater_mode.constants import (
    DEFAULT_DIM_CURVE,
    DEFAULT_DIM_DURATION,
    DEFAULT_DIM_FACTOR,
    DIMMER_BINARY_NAME,
)
from theater_mode.display.drm import output_modes
from theater_mode.effects.base import Effect, EffectOptions
from theater_mode.steam import build_artwork

log = logging.getLogger("theater-moded")


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
        dim_factor: float = DEFAULT_DIM_FACTOR,
        duration: float = DEFAULT_DIM_DURATION,
        curve: str = DEFAULT_DIM_CURVE,
        art: bool = True,
        binary_path: Path | str | None = None,
    ) -> None:
        self._dim_factor = dim_factor
        self._duration = duration
        self._curve = curve.lower()
        self._art = art
        self._custom_binary = Path(binary_path) if binary_path else None
        self._process: subprocess.Popen[str] | None = None
        self._dimmed = False

    @classmethod
    def create(cls, options: EffectOptions) -> DimEffect:
        return cls(
            dim_factor=options.dim_factor,
            duration=options.dim_duration,
            curve=options.dim_curve,
            art=options.art,
        )

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
        try:
            if self._process.stdin:
                self._process.stdin.close()
        except OSError:
            pass
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

    def artwork_for(self, appid: str, size: tuple[int, int] | None) -> Path | None:
        """Build this game's artwork for one output size, or None if unavailable."""
        if not self._art or not appid or size is None:
            return None
        return build_artwork(appid, size[0], size[1], self._dim_factor)

    @staticmethod
    def art_command(output: str, artwork: Path | None, size: tuple[int, int] | None) -> str:
        """Format the ART protocol command for an output."""
        if artwork is None or size is None:
            return f"ART {output}"
        return f"ART {output} {size[0]} {size[1]} {artwork}"

    def apply(self, game_output: str, other_outputs: list[str], appid: str) -> None:
        if not other_outputs:
            log.info("no secondary outputs to dim")
            self.revert()
            return

        targets = sorted(other_outputs)
        # Read the connector modes once, not once per output.
        sizes = output_modes() if self._art else {}

        with_art: list[str] = []
        for output in targets:
            size = sizes.get(output)
            artwork = self.artwork_for(appid, size)
            self._send(self.art_command(output, artwork, size))
            if artwork is not None:
                with_art.append(output)

        log.info(
            "cinematic dimming on [%s] (factor=%.2f, duration=%.1fs, curve=%s, artwork on: %s)",
            ", ".join(targets),
            self._dim_factor,
            self._duration,
            self._curve,
            ", ".join(with_art) or "none",
        )

        joined = ",".join(targets)
        if self._send(f"DIM {joined} {self._dim_factor:.3f} {self._duration:.2f} {self._curve}"):
            self._dimmed = True

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
