"""D-Bus service dispatching and method invocation handling."""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Callable
from typing import Any

from theater_mode.constants import INTERFACE
from theater_mode.daemon import Daemon

log = logging.getLogger("theater-moded")

VariantFactory = Callable[[str, tuple[Any, ...]], Any]


def make_handler(daemon: Daemon, make_variant: VariantFactory) -> Callable[..., None]:
    """Create a D-Bus method dispatch closure bound to a Daemon instance."""

    def handle_call(
        conn: Any,
        sender: str,
        path: str,
        iface: str,
        method: str,
        params: Any,
        invocation: Any,
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
                    invocation.return_value(make_variant("(s)", (daemon.status(),)))
                    return
                case "Simulate":
                    invocation.return_value(make_variant("(s)", (daemon.simulate(*args),)))
                    return
                case "Clear":
                    invocation.return_value(make_variant("(s)", (daemon.clear(),)))
                    return
                case "GetOutputs":
                    invocation.return_value(make_variant("(s)", (daemon.get_outputs(),)))
                    return
                case "GetResolved":
                    invocation.return_value(make_variant("(s)", (daemon.get_resolved(),)))
                    return
                case "GetDiagnostics":
                    invocation.return_value(make_variant("(s)", (daemon.get_diagnostics(),)))
                    return
                case "Preview":
                    invocation.return_value(make_variant("(s)", (daemon.preview(*args),)))
                    return
                case "RevertPreview":
                    invocation.return_value(make_variant("(s)", (daemon.revert_preview(),)))
                    return
                case "Commit":
                    invocation.return_value(make_variant("(s)", (daemon.commit(*args),)))
                    return
                case "Reload":
                    invocation.return_value(make_variant("(s)", (daemon.reload(),)))
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
