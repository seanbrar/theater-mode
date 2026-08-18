"""Command-line interface, process lifecycle, and application entry point."""

from __future__ import annotations

import argparse
import logging
import signal
import sys

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
gi.require_version("GLibUnix", "2.0")
from gi.repository import Gio, GLib, GLibUnix  # noqa: E402

from theater_mode import __version__  # noqa: E402
from theater_mode.constants import (  # noqa: E402
    BUS_NAME,
    DEFAULT_DIM_CURVE,
    DEFAULT_DIM_DURATION,
    DEFAULT_DIM_FACTOR,
    INTERFACE_XML,
    OBJECT_PATH,
)
from theater_mode.daemon import Daemon  # noqa: E402
from theater_mode.effects import EFFECTS, EffectOptions  # noqa: E402
from theater_mode.service import make_handler  # noqa: E402

log = logging.getLogger("theater-moded")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse and validate command-line arguments."""
    parser = argparse.ArgumentParser(
        prog="theater-moded",
        description="Smart multi-monitor theater mode daemon for KDE Plasma on Wayland.",
    )
    parser.add_argument("--version", action="version", version=f"theater-moded {__version__}")
    parser.add_argument(
        "--effect",
        default="log",
        choices=sorted(EFFECTS),
        help="effect to apply to secondary outputs; default is log (dry run)",
    )
    parser.add_argument(
        "--dim-factor",
        type=float,
        default=DEFAULT_DIM_FACTOR,
        help="how much of a secondary output's brightness to remove "
        f"(0 = no dimming, 1 = fully black; default: {DEFAULT_DIM_FACTOR})",
    )
    parser.add_argument(
        "--dim-duration",
        type=float,
        default=DEFAULT_DIM_DURATION,
        help=f"duration in seconds for cinematic fade transitions (default: {DEFAULT_DIM_DURATION}s)",
    )
    parser.add_argument(
        "--dim-curve",
        type=str,
        default=DEFAULT_DIM_CURVE,
        choices=["sine", "quad", "cubic", "linear"],
        help=f"mathematical easing curve for fades (default: {DEFAULT_DIM_CURVE})",
    )
    parser.add_argument(
        "--art",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="show the game's Steam library artwork on dimmed outputs (default: enabled)",
    )
    parser.add_argument(
        "--revert-delay",
        type=float,
        default=3.0,
        help="grace period (seconds) before reverting after game windows close (0 disables)",
    )
    parser.add_argument(
        "--stage-delay",
        type=float,
        default=1.5,
        help="stability delay (seconds) before following a game to a new display output",
    )
    parser.add_argument(
        "--require-fullscreen",
        action="store_true",
        help="only treat a game as active once its window enters fullscreen",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="enable verbose debug logging for all window events",
    )
    args = parser.parse_args(argv)

    if not 0.0 <= args.dim_factor <= 1.0:
        parser.error("--dim-factor must be between 0 (no dimming) and 1 (fully black)")

    if args.dim_duration <= 0.0:
        parser.error("--dim-duration must be greater than 0")

    return args


def main(argv: list[str] | None = None) -> int:
    """Run the theater-moded application."""
    args = parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
        stream=sys.stderr,
    )

    effect = EFFECTS[args.effect].create(
        EffectOptions(
            dim_factor=args.dim_factor,
            dim_duration=args.dim_duration,
            dim_curve=args.dim_curve,
            art=args.art,
        )
    )

    daemon = Daemon(
        effect=effect,
        require_fullscreen=args.require_fullscreen,
        revert_delay=max(0.0, args.revert_delay),
        stage_delay=max(0.0, args.stage_delay),
    )

    loop = GLib.MainLoop()

    def shutdown(*_: object) -> bool:
        """Handle termination signals and ensure displays are restored immediately."""
        log.info("shutting down; reverting active effects")
        try:
            daemon.clear(immediate=True)
        finally:
            loop.quit()
        return GLib.SOURCE_REMOVE

    GLibUnix.signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, shutdown)
    GLibUnix.signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, shutdown)

    node = Gio.DBusNodeInfo.new_for_xml(INTERFACE_XML)
    registration: dict[str, int] = {}

    def on_bus_acquired(conn: Gio.DBusConnection, name: str, *_: object) -> None:
        registration["id"] = conn.register_object_with_closures2(
            OBJECT_PATH, node.interfaces[0], make_handler(daemon), None, None
        )
        log.info("listening on %s as %s (effect: %s)", OBJECT_PATH, name, effect.name)

    def on_name_lost(*_: object) -> None:
        log.error("lost D-Bus name %s; another instance may be active", BUS_NAME)
        loop.quit()

    Gio.bus_own_name(
        Gio.BusType.SESSION,
        BUS_NAME,
        Gio.BusNameOwnerFlags.NONE,
        on_bus_acquired,
        None,
        on_name_lost,
    )

    loop.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
