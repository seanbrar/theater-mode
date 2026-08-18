"""D-Bus service dispatching and method invocation handling."""

from __future__ import annotations

import contextlib
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
            match method:
                case "WindowOpened":
                    daemon.window_opened(*args)
                case "WindowChanged":
                    daemon.window_changed(*args)
                case "WindowClosed":
                    daemon.window_closed(*args)
                case "SnapshotBegin":
                    daemon.snapshot_begin()
                case "SnapshotEnd":
                    daemon.snapshot_end()
                case "Status":
                    invocation.return_value(GLib.Variant("(s)", (daemon.status(),)))
                    return
                case "Simulate":
                    invocation.return_value(GLib.Variant("(s)", (daemon.simulate(*args),)))
                    return
                case "Clear":
                    invocation.return_value(GLib.Variant("(s)", (daemon.clear(),)))
                    return
                case "GetOutputs":
                    invocation.return_value(GLib.Variant("(s)", (daemon.get_outputs(),)))
                    return
                case "GetResolved":
                    invocation.return_value(GLib.Variant("(s)", (daemon.get_resolved(),)))
                    return
                case "GetDiagnostics":
                    invocation.return_value(GLib.Variant("(s)", (daemon.get_diagnostics(),)))
                    return
                case "Preview":
                    invocation.return_value(GLib.Variant("(s)", (daemon.preview(*args),)))
                    return
                case "RevertPreview":
                    invocation.return_value(GLib.Variant("(s)", (daemon.revert_preview(),)))
                    return
                case "Commit":
                    invocation.return_value(GLib.Variant("(s)", (daemon.commit(*args),)))
                    return
                case "Reload":
                    invocation.return_value(GLib.Variant("(s)", (daemon.reload(),)))
                    return
                case _:
                    invocation.return_dbus_error(f"{INTERFACE}.UnknownMethod", method)
                    return

            invocation.return_value(None)
        except Exception:
            # Prevent unhandled exceptions in individual window events from terminating the daemon
            log.exception("error handling D-Bus method %s", method)
            with contextlib.suppress(Exception):
                invocation.return_dbus_error(f"{INTERFACE}.Failed", f"{method} failed")

    return handle_call
