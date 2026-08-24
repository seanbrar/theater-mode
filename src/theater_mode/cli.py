"""Command-line interface, process lifecycle, and application entry point for theater-moded."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from theater_mode import __version__
from theater_mode.bus import EventLoop, serve
from theater_mode.config import (
    DevConfig,
    get_dev_config,
    load_resolved_config,
)
from theater_mode.daemon import Daemon
from theater_mode.effects import DimEffect, EffectOptions

log = logging.getLogger("theater-moded")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse and validate command-line arguments (Dev keys only)."""
    parser = argparse.ArgumentParser(
        prog="theater-moded",
        description="Smart multi-monitor theater mode daemon for KDE Plasma on Wayland.",
    )
    parser.add_argument("--version", action="version", version=f"theater-moded {__version__}")
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="enable verbose debug logging for all window and effect events (Dev key)",
    )
    parser.add_argument(
        "--replace-user-config",
        type=Path,
        dest="user_config_override",
        help="path to replacement user configuration file for testing (Dev key)",
    )
    parser.add_argument(
        "--replace-system-config",
        type=Path,
        dest="system_config_override",
        help="path to replacement system configuration file for testing (Dev key)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the theater-moded application."""
    args = parse_args(argv)

    env_dev = get_dev_config()
    dev_config = DevConfig(
        user_config_override=args.user_config_override or env_dev.user_config_override,
        system_config_override=args.system_config_override or env_dev.system_config_override,
        force_art_dir=env_dev.force_art_dir,
        verbose=args.verbose or env_dev.verbose,
    )

    logging.basicConfig(
        level=logging.DEBUG if dev_config.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
        stream=sys.stderr,
    )

    resolved_config, diagnostics = load_resolved_config(dev_config=dev_config)
    for d in diagnostics:
        log.warning(
            "config %s: %s (file: %s, line: %s)",
            d.severity,
            d.message,
            d.source_file or "global",
            d.line_number or "-",
        )

    effect = DimEffect.create(EffectOptions.from_config(resolved_config))

    loop = EventLoop()
    daemon = Daemon(
        effect=effect,
        config=resolved_config,
        diagnostics=diagnostics,
        dev_config=dev_config,
        scheduler=loop,
    )

    return serve(daemon, loop, effect.name)


if __name__ == "__main__":
    sys.exit(main())
