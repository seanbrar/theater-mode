"""Unit tests for D-Bus service method dispatcher."""

from __future__ import annotations

import unittest
import xml.etree.ElementTree as ET
from unittest.mock import MagicMock

from theater_mode.constants import INTERFACE, INTERFACE_XML, OBJECT_PATH
from theater_mode.service import make_handler


class FakeVariant:
    def __init__(self, *args: object) -> None:
        self._args = args

    def unpack(self) -> tuple[object, ...]:
        return self._args


class TestServiceDispatch(unittest.TestCase):
    def setUp(self) -> None:
        self.mock_daemon = MagicMock()
        self.make_variant = MagicMock(side_effect=lambda fmt, values: (fmt, values))
        self.handler = make_handler(self.mock_daemon, self.make_variant)
        self.mock_conn = MagicMock()
        self.mock_invocation = MagicMock()

    def _call(self, method: str, params: FakeVariant | None = None) -> None:
        self.handler(
            self.mock_conn,
            ":1.42",
            OBJECT_PATH,
            INTERFACE,
            method,
            params,
            self.mock_invocation,
        )

    def test_window_lifecycle_methods(self) -> None:
        self._call("WindowOpened", FakeVariant("win-1", "steam_app_100", "1000", "DP-1", "true"))
        self.mock_daemon.window_opened.assert_called_once_with(
            "win-1", "steam_app_100", "1000", "DP-1", "true"
        )
        self.mock_invocation.return_value.assert_called_with(None)

        self._call("WindowChanged", FakeVariant("win-1", "DP-2", "true"))
        self.mock_daemon.window_changed.assert_called_once_with("win-1", "DP-2", "true")

        self._call("WindowClosed", FakeVariant("win-1"))
        self.mock_daemon.window_closed.assert_called_once_with("win-1")

        self._call("SnapshotBegin", FakeVariant("DP-1,DP-2"))
        self.mock_daemon.snapshot_begin.assert_called_once_with("DP-1,DP-2")
        self._call("SnapshotEnd")
        self.mock_daemon.snapshot_end.assert_called_once()

    def test_snapshot_begin_interface_accepts_screen_names(self) -> None:
        root = ET.fromstring(INTERFACE_XML)
        method = root.find(f"./interface[@name='{INTERFACE}']/method[@name='SnapshotBegin']")
        self.assertIsNotNone(method)
        args = method.findall("arg") if method is not None else []
        self.assertEqual(
            [(arg.get("name"), arg.get("type"), arg.get("direction")) for arg in args],
            [("screens", "s", "in")],
        )

    def test_daemon_query_and_control_methods(self) -> None:
        self.mock_daemon.status.return_value = "Active"
        self._call("Status")
        self.mock_daemon.status.assert_called_once()
        self.make_variant.assert_called_with("(s)", ("Active",))
        self.mock_invocation.return_value.assert_called_with(("(s)", ("Active",)))

        self.mock_daemon.simulate.return_value = "Simulated"
        self._call("Simulate", FakeVariant("1671210", "DP-1"))
        self.mock_daemon.simulate.assert_called_once_with("1671210", "DP-1")

        self.mock_daemon.clear.return_value = "Cleared"
        self._call("Clear")
        self.mock_daemon.clear.assert_called_once()

        self.mock_daemon.get_outputs.return_value = "[]"
        self._call("GetOutputs")
        self.mock_daemon.get_outputs.assert_called_once()

        self.mock_daemon.get_resolved.return_value = "{}"
        self._call("GetResolved")
        self.mock_daemon.get_resolved.assert_called_once()

        self.mock_daemon.get_diagnostics.return_value = "[]"
        self._call("GetDiagnostics")
        self.mock_daemon.get_diagnostics.assert_called_once()

        self.mock_daemon.preview.return_value = "Previewed"
        self._call("Preview", FakeVariant('{"effect.dimming": 0.5}'))
        self.mock_daemon.preview.assert_called_once_with('{"effect.dimming": 0.5}')

        self.mock_daemon.revert_preview.return_value = "Reverted"
        self._call("RevertPreview")
        self.mock_daemon.revert_preview.assert_called_once()

        self.mock_daemon.commit.return_value = "Committed"
        self._call("Commit", FakeVariant('{"effect.dimming": 0.5}'))
        self.mock_daemon.commit.assert_called_once_with('{"effect.dimming": 0.5}')

        self.mock_daemon.reload.return_value = "Reloaded"
        self._call("Reload")
        self.mock_daemon.reload.assert_called_once()

    def test_unknown_method_returns_dbus_error(self) -> None:
        self._call("NonExistentMethod")
        self.mock_invocation.return_dbus_error.assert_called_once_with(
            f"{INTERFACE}.UnknownMethod", "NonExistentMethod"
        )

    def test_exception_in_handler_returns_failed_error(self) -> None:
        self.mock_daemon.status.side_effect = RuntimeError("database locked")
        with self.assertLogs("theater-moded", level="ERROR"):
            self._call("Status")
        self.mock_invocation.return_dbus_error.assert_called_once_with(
            f"{INTERFACE}.Failed", "Status failed"
        )


if __name__ == "__main__":
    unittest.main()
