"""Command-line interface, process lifecycle, and application entry point for theater-moded."""

from __future__ import annotations

import argparse
import logging
import signal
import sys
from pathlib import Path

from theater_mode import __version__
from theater_mode.config import (
    DevConfig,
    get_dev_config,
    load_resolved_config,
)
from theater_mode.constants import (
    BUS_NAME,
    INTERFACE,
    INTERFACE_XML,
    OBJECT_PATH,
)
from theater_mode.daemon import Daemon
from theater_mode.effects import DimEffect, EffectOptions
from theater_mode.service import make_handler

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

    daemon = Daemon(
        effect=effect,
        config=resolved_config,
        diagnostics=diagnostics,
        dev_config=dev_config,
    )

    import gi

    gi.require_version("Gio", "2.0")
    gi.require_version("GLib", "2.0")
    gi.require_version("GLibUnix", "2.0")
    from gi.repository import Gio, GLib, GLibUnix

    loop = GLib.MainLoop()
    exit_code = 0

    def shutdown(*_: object) -> bool:
        """Handle termination signals and ensure displays are restored immediately."""
        nonlocal exit_code
        exit_code = 0
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
    active_connection: list[Gio.DBusConnection] = []

    def on_config_changed_signal() -> None:
        if active_connection:
            try:
                active_connection[0].emit_signal(
                    None,
                    OBJECT_PATH,
                    INTERFACE,
                    "ConfigChanged",
                    None,
                )
            except Exception:
                log.exception("failed to emit ConfigChanged signal")

    daemon.on_config_changed = on_config_changed_signal

    def on_bus_acquired(conn: Gio.DBusConnection, name: str, *_: object) -> None:
        active_connection.append(conn)
        registration["id"] = conn.register_object_with_closures2(
            OBJECT_PATH, node.interfaces[0], make_handler(daemon, GLib.Variant), None, None
        )
        log.info("listening on %s as %s (effect: %s)", OBJECT_PATH, name, effect.name)

    def on_name_lost(*_: object) -> None:
        nonlocal exit_code
        log.error("lost D-Bus name %s; another instance may be active", BUS_NAME)
        exit_code = 1
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
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
