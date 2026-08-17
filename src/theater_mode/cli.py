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

from theater_mode.constants import BUS_NAME, INTERFACE_XML, OBJECT_PATH
from theater_mode.daemon import Daemon
from theater_mode.effects import (
    EFFECTS,
    BrightnessEffect,
    CompositeEffect,
    Effect,
)
from theater_mode.service import make_handler

log = logging.getLogger("theater-moded")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse and validate command-line arguments."""
    parser = argparse.ArgumentParser(
        prog="theater-moded",
        description="Smart multi-monitor theater mode daemon for KDE Plasma on Wayland.",
    )
    parser.add_argument(
        "--effect",
        default="log",
        help="effects to apply to secondary outputs, comma separated "
        f"({', '.join(sorted(EFFECTS))}); default is log (dry run)",
    )
    parser.add_argument(
        "--dim-factor",
        type=float,
        default=0.35,
        help="fraction of original brightness to dim to for brightness effect (0 < x <= 1)",
    )
    parser.add_argument(
        "--settle-seconds",
        type=float,
        default=1.5,
        help="hardware brightness transition settle delay (used to sequence wallpaper switches)",
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

    if not 0.0 < args.dim_factor <= 1.0:
        parser.error("--dim-factor must be greater than 0 and at most 1")

    return args


def build_effect_pipeline(effect_str: str, dim_factor: float, settle_seconds: float) -> Effect:
    """Instantiate and compose requested effects from comma-separated names."""
    names = [name.strip() for name in effect_str.split(",") if name.strip()]
    unknown = [name for name in names if name not in EFFECTS]
    if unknown or not names:
        raise ValueError(
            f"unknown effect(s): {', '.join(unknown) or '(none)'}; "
            f"choose from {', '.join(sorted(EFFECTS))}"
        )

    def build_single(name: str) -> Effect:
        if EFFECTS[name] is BrightnessEffect:
            return BrightnessEffect(dim_factor, settle_seconds=settle_seconds)
        return EFFECTS[name]()

    built = [build_single(name) for name in names]
    return built[0] if len(built) == 1 else CompositeEffect(built)


def main(argv: list[str] | None = None) -> int:
    """Run the theater-moded application."""
    args = parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
        stream=sys.stderr,
    )

    try:
        effect = build_effect_pipeline(
            args.effect,
            dim_factor=args.dim_factor,
            settle_seconds=args.settle_seconds,
        )
    except ValueError as exc:
        log.error("%s", exc)
        return 2

    daemon = Daemon(
        effect=effect,
        require_fullscreen=args.require_fullscreen,
        revert_delay=max(0.0, args.revert_delay),
        stage_delay=max(0.0, args.stage_delay),
    )
    daemon.recover()

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
