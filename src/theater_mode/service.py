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
    void_methods: dict[str, Callable[..., Any]] = {
        "WindowOpened": daemon.window_opened,
        "WindowChanged": daemon.window_changed,
        "WindowClosed": daemon.window_closed,
        "SnapshotBegin": daemon.snapshot_begin,
        "SnapshotEnd": daemon.snapshot_end,
    }
    string_methods: dict[str, Callable[..., str]] = {
        "Status": daemon.status,
        "Simulate": daemon.simulate,
        "Clear": daemon.clear,
        "GetOutputs": daemon.get_outputs,
        "GetResolved": daemon.get_resolved,
        "GetDiagnostics": daemon.get_diagnostics,
        "Preview": daemon.preview,
        "RevertPreview": daemon.revert_preview,
        "Commit": daemon.commit,
        "Reload": daemon.reload,
    }

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
            if fn := void_methods.get(method):
                fn(*args)
                invocation.return_value(None)
            elif fn := string_methods.get(method):
                invocation.return_value(make_variant("(s)", (fn(*args),)))
            else:
                invocation.return_dbus_error(f"{INTERFACE}.UnknownMethod", method)
        except Exception:
            # Prevent unhandled exceptions in individual window events from terminating the daemon
            log.exception("error handling D-Bus method %s", method)
            with contextlib.suppress(Exception):
                invocation.return_dbus_error(f"{INTERFACE}.Failed", f"{method} failed")

    return handle_call
