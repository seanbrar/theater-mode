"""D-Bus service dispatching and method invocation handling."""

from __future__ import annotations

import logging
from collections.abc import Callable

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from theater_mode.constants import INTERFACE
from theater_mode.daemon import Daemon

log = logging.getLogger("theater-moded")


def make_handler(daemon: Daemon) -> Callable:
    """Create a D-Bus method dispatch closure bound to a Daemon instance."""

    def handle_call(
        conn: Gio.DBusConnection,
        sender: str,
        path: str,
        iface: str,
        method: str,
        params: GLib.Variant,
        invocation: Gio.DBusMethodInvocation,
        *_: object,
    ) -> None:
        try:
            args = params.unpack() if params is not None else ()
            if method == "WindowOpened":
                daemon.window_opened(*args)
            elif method == "WindowChanged":
                daemon.window_changed(*args)
            elif method == "WindowClosed":
                daemon.window_closed(*args)
            elif method == "SnapshotBegin":
                daemon.snapshot_begin()
            elif method == "SnapshotEnd":
                daemon.snapshot_end()
            elif method == "Status":
                invocation.return_value(GLib.Variant("(s)", (daemon.status(),)))
                return
            elif method == "Simulate":
                invocation.return_value(GLib.Variant("(s)", (daemon.simulate(*args),)))
                return
            elif method == "Clear":
                invocation.return_value(GLib.Variant("(s)", (daemon.clear(),)))
                return
            else:
                invocation.return_dbus_error(f"{INTERFACE}.UnknownMethod", method)
                return

            invocation.return_value(None)
        except Exception:
            # Prevent unhandled exceptions in individual window events from terminating the daemon
            log.exception("error handling D-Bus method %s", method)
            try:
                invocation.return_dbus_error(f"{INTERFACE}.Failed", f"{method} failed")
            except Exception:
                pass

    return handle_call
