"""Session bus hosting and the daemon's event loop."""

from __future__ import annotations

import logging
import selectors
import signal
import socket
import time
from collections.abc import Callable
from itertools import count
from typing import Any

from theater_mode._vendor.jeepney import (
    DBusAddress,
    HeaderFields,
    Message,
    MessageType,
    new_error,
    new_method_return,
    new_signal,
)
from theater_mode._vendor.jeepney.bus_messages import DBusNameFlags, message_bus
from theater_mode._vendor.jeepney.io.blocking import DBusConnection, open_dbus_connection
from theater_mode.constants import BUS_NAME, INTERFACE, INTERFACE_XML, OBJECT_PATH
from theater_mode.daemon import Daemon
from theater_mode.service import make_handler

log = logging.getLogger("theater-moded")

INTROSPECTABLE = "org.freedesktop.DBus.Introspectable"

# org.freedesktop.DBus.RequestName reply codes.
NAME_PRIMARY_OWNER = 1


class EventLoop:
    """Selector-driven loop supplying the daemon's timers and socket readiness.

    Implements `TimerScheduler`. Every callback runs on the loop thread, so a callback may
    schedule timers, cancel them, or call `quit` without further synchronization.
    """

    def __init__(self) -> None:
        self._selector = selectors.DefaultSelector()
        self._timers: dict[int, tuple[float, Callable[[], None]]] = {}
        self._tokens = count(1)
        self._running = False

    def add_reader(self, sock: socket.socket, callback: Callable[[], None]) -> None:
        """Invoke callback whenever sock becomes readable."""
        self._selector.register(sock, selectors.EVENT_READ, callback)

    def timeout_add(self, delay_ms: int, callback: Callable[[], None]) -> int:
        """Schedule a one-shot callback, returning the token that cancels it."""
        token = next(self._tokens)
        self._timers[token] = (time.monotonic() + delay_ms / 1000.0, callback)
        return token

    def source_remove(self, tag: Any) -> None:
        """Cancel a pending timer. A token that already fired is silently ignored."""
        self._timers.pop(tag, None)

    def quit(self) -> None:
        """Stop the loop after the current pass completes."""
        self._running = False

    def run(self) -> None:
        """Service timers and readable sockets until quit is called."""
        self._running = True
        try:
            while self._running:
                events = self._selector.select(self._next_delay())
                self._fire_expired_timers()
                for key, _ in events:
                    if not self._running:
                        break
                    key.data()
        finally:
            self._selector.close()

    def _next_delay(self) -> float | None:
        """Return seconds until the earliest timer, or None to wait indefinitely."""
        if not self._timers:
            return None
        earliest = min(deadline for deadline, _ in self._timers.values())
        return max(0.0, earliest - time.monotonic())

    def _fire_expired_timers(self) -> None:
        now = time.monotonic()
        due = sorted(
            (deadline, token) for token, (deadline, _) in self._timers.items() if deadline <= now
        )
        for _, token in due:
            # An earlier callback in this pass may have cancelled a later one.
            entry = self._timers.pop(token, None)
            if entry is not None:
                entry[1]()


class _Reply:
    """Answers one method call, presenting the invocation interface `service` expects."""

    def __init__(self, conn: DBusConnection, call: Message) -> None:
        self._conn = conn
        self._call = call

    def return_value(self, text: str | None) -> None:
        if text is None:
            self._conn.send(new_method_return(self._call))
        else:
            self._conn.send(new_method_return(self._call, "s", (text,)))

    def return_dbus_error(self, name: str, message: str) -> None:
        self._conn.send(new_error(self._call, name, "s", (message,)))


def _install_signal_handlers(loop: EventLoop, on_signal: Callable[[], None]) -> socket.socket:
    """Route SIGINT and SIGTERM into the loop, returning the socket to keep alive.

    The handlers themselves do nothing: `set_wakeup_fd` writes the signal number to the
    socket, and the loop dispatches from there. Shutdown reverts active effects, which has
    to run on the loop thread rather than inside a signal handler.
    """
    reader, writer = socket.socketpair()
    for sock in (reader, writer):
        sock.setblocking(False)
    signal.set_wakeup_fd(writer.fileno())

    def _noop(signum: int, frame: object) -> None:
        """Exist so CPython writes the signal to the wakeup socket."""

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, _noop)

    def _on_readable() -> None:
        try:
            reader.recv(4096)
        except BlockingIOError:
            return
        on_signal()

    loop.add_reader(reader, _on_readable)
    # The writer must outlive this call: set_wakeup_fd holds a bare descriptor, and closing
    # the socket here would leave CPython writing into a closed or recycled fd.
    return writer


def _dispatch(conn: DBusConnection, handler: Callable[..., None], call: Message) -> None:
    fields = call.header.fields
    interface = fields.get(HeaderFields.interface)
    member = fields.get(HeaderFields.member)

    if interface == INTROSPECTABLE and member == "Introspect":
        conn.send(new_method_return(call, "s", (INTERFACE_XML,)))
        return

    handler(
        conn,
        fields.get(HeaderFields.sender, ""),
        fields.get(HeaderFields.path, OBJECT_PATH),
        interface,
        member,
        call.body,
        _Reply(conn, call),
    )


def _drain(conn: DBusConnection, handler: Callable[..., None], loop: EventLoop) -> None:
    """Dispatch every message the last read produced.

    One readable event can carry several messages, and the parser holds the remainder with
    no further socket activity to announce them. Draining until the parser is empty is what
    keeps a queued call from waiting for unrelated traffic to wake the loop.
    """
    while True:
        try:
            message = conn.receive(timeout=0)
        except TimeoutError:
            return
        except (OSError, EOFError) as exc:
            log.error("lost the session bus connection: %s", exc)
            loop.quit()
            return

        if message.header.message_type is MessageType.method_call:
            _dispatch(conn, handler, message)


def serve(daemon: Daemon, loop: EventLoop, effect_name: str) -> int:
    """Own the bus name and service the daemon until a termination signal arrives.

    Returns the process exit status: non-zero when the name could not be claimed, which
    means another instance holds it.
    """
    try:
        conn = open_dbus_connection(bus="SESSION")
    except Exception as exc:
        log.error("could not connect to the session bus: %s", exc)
        return 1

    reply = conn.send_and_get_reply(message_bus.RequestName(BUS_NAME, DBusNameFlags.do_not_queue))
    if reply.body[0] != NAME_PRIMARY_OWNER:
        log.error("could not take D-Bus name %s; another instance may be active", BUS_NAME)
        conn.close()
        return 1

    signal_address = DBusAddress(OBJECT_PATH, bus_name=BUS_NAME, interface=INTERFACE)

    def emit_config_changed() -> None:
        try:
            conn.send(new_signal(signal_address, "ConfigChanged"))
        except OSError:
            log.exception("failed to emit ConfigChanged signal")

    daemon.on_config_changed = emit_config_changed

    def shutdown() -> None:
        log.info("shutting down; reverting active effects")
        try:
            daemon.clear(immediate=True)
        finally:
            loop.quit()

    handler = make_handler(daemon)
    loop.add_reader(conn.sock, lambda: _drain(conn, handler, loop))
    wakeup = _install_signal_handlers(loop, shutdown)

    log.info("listening on %s as %s (effect: %s)", OBJECT_PATH, BUS_NAME, effect_name)
    try:
        loop.run()
    finally:
        signal.set_wakeup_fd(-1)
        wakeup.close()
        conn.close()
    return 0
