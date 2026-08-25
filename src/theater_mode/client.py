"""Command-line client for theater-mode D-Bus control and configuration management."""

from __future__ import annotations

import argparse
import contextlib
import json
import subprocess
import sys
from collections.abc import Callable
from typing import Any

from theater_mode import __version__
from theater_mode.config.loader import lookup_spec, split_key_path
from theater_mode.constants import APP_DATA, BUS_NAME, INTERFACE, OBJECT_PATH
from theater_mode.utils import plural

_NOT_RUNNING_ERRORS = frozenset(
    {
        "org.freedesktop.DBus.Error.ServiceUnknown",
        "org.freedesktop.DBus.Error.NameHasNoOwner",
    }
)


def _call_dbus_method(method_name: str, *args: Any) -> str:
    """Invoke a D-Bus method on the active theater-mode daemon and return the string response."""
    from theater_mode._vendor.jeepney import DBusAddress, DBusErrorResponse, new_method_call
    from theater_mode._vendor.jeepney.io.blocking import open_dbus_connection

    # unwrap_msg is absent from the package's __all__, so it comes from its own module.
    from theater_mode._vendor.jeepney.wrappers import unwrap_msg

    address = DBusAddress(OBJECT_PATH, bus_name=BUS_NAME, interface=INTERFACE)
    try:
        with open_dbus_connection(bus="SESSION") as conn:
            call = new_method_call(
                address, method_name, "s" * len(args), tuple(str(a) for a in args)
            )
            body = unwrap_msg(conn.send_and_get_reply(call, timeout=5))
        return str(body[0])
    except DBusErrorResponse as e:
        # Only an unowned D-Bus name means the service is stopped. Every other error keeps
        # its own text, so a real failure is not disguised as a missing service.
        if e.name in _NOT_RUNNING_ERRORS:
            print(
                "error: the theater-mode background service is not running.\n"
                "  Start it with: systemctl --user restart theater-mode.service\n"
                "  Then check:    theater-mode doctor",
                file=sys.stderr,
            )
        else:
            print(
                f"error: could not reach the theater-mode background service: {e}", file=sys.stderr
            )
        sys.exit(1)
    except (OSError, TimeoutError, ValueError) as e:
        print(f"error: could not reach the theater-mode background service: {e}", file=sys.stderr)
        sys.exit(1)


RULE = " " + "-" * 77


def _display_value(value: Any) -> str:
    """Format values using TOML spelling (e.g. lowercase booleans)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _format_provenance_table(config_data: dict[str, Any]) -> str:
    """Format the resolved configuration and its provenance into a human-readable table."""
    provenance = config_data.get("provenance", {})
    outputs = config_data.get("outputs", {})

    def row(key_path: str, value: Any, default_layer: str) -> str:
        prov = provenance.get(key_path, {})
        file_src, line = prov.get("file"), prov.get("line")
        source = f"{file_src}:{line}" if file_src and line else (file_src or "-")
        rendered = _display_value(value)
        return f" {key_path:<32} {rendered:<20} {prov.get('layer', default_layer):<10} {source}"

    lines: list[str] = [
        " theater-mode Resolved Configuration",
        RULE,
        f" {'KEY PATH':<32} {'RESOLVED VALUE':<20} {'LAYER':<10} {'SOURCE FILE:LINE'}",
        RULE,
    ]
    lines += [
        row(f"{section}.{key}", value, "builtin")
        for section in ("effect", "transition", "behavior")
        for key, value in config_data.get(section, {}).items()
    ]

    if outputs:
        lines += [RULE, " Per-Output Overrides:"]
        lines += [
            row(f"outputs.{out_id}.{leaf}", value, "user")
            for out_id, table in outputs.items()
            for leaf, value in table.items()
        ]

    lines.append(RULE)
    return "\n".join(lines)


def _format_diagnostics(diag_list: list[dict[str, Any]]) -> str:
    """Format configuration diagnostics into readable output."""
    if not diag_list:
        return "✓ No configuration diagnostics or warnings."

    lines: list[str] = [f"Found {plural(len(diag_list), 'configuration diagnostic')}:\n"]
    for idx, d in enumerate(diag_list, start=1):
        sev = d.get("severity", "error").upper()
        msg = d.get("message", "")
        file_path = d.get("file")
        line = d.get("line")
        loc = f"{file_path}:{line}" if (file_path and line) else (file_path or "global")

        lines.append(f" [{idx}] [{sev}] {msg}")
        lines.append(f"     Location: {loc}")
        if d.get("offending_value") is not None:
            lines.append(f"     Offending Value: {d['offending_value']}")
        if d.get("substituted_value") is not None:
            lines.append(f"     Substituted Default: {d['substituted_value']}")
        lines.append("")
    return "\n".join(lines)


def _format_outputs(outputs: list[dict[str, Any]]) -> str:
    """List connected displays with the configuration keys that address each one."""
    if not outputs:
        return "No connected outputs reported."

    lines: list[str] = []
    for output in outputs:
        marker = "  (game display)" if output.get("active") else ""
        described = " ".join(
            str(part)
            for part in (output.get("vendor") or output.get("pnp_id"), output.get("model"))
            if part
        )
        lines.append(f" {output['connector']}{marker}")
        lines.append(f"   {described or 'no EDID reported'}")
        if output.get("serial"):
            lines.append(f"   serial: {output['serial']}")

        keys = output.get("match_keys") or []
        if keys:
            lines.append("   config sections, most specific first:")
            lines += [f'     [outputs."{key}"]' for key in keys]
        lines.append(f"     [outputs.{output['connector']}]")
        lines.append("")

    lines.append("Rules are matched top to bottom; the first section that exists wins.")
    return "\n".join(lines)


def _format_status(data: dict[str, Any]) -> str:
    """Summarize daemon state in plain language.

    Reports only what the daemon tracks. Games are named by AppID because nothing in
    the pipeline resolves an AppID to its store title.
    """
    dimmed = data.get("affected_outputs") or []
    games = data.get("games") or []
    outputs = data.get("outputs") or []
    secondary = data.get("secondary_outputs") or []

    if dimmed:
        headline = f"theater-mode is dimming {plural(len(dimmed), 'display')}."
    elif data.get("restore_pending"):
        headline = "theater-mode is restoring your displays."
    elif games and data.get("require_fullscreen") and data.get("active_output") is None:
        headline = "theater-mode is waiting for the game to enter fullscreen."
    elif games and data.get("active_output") is not None and secondary:
        headline = "theater-mode sees the game, but dimming is zero on every other display."
    elif games and data.get("active_output") is not None:
        headline = "theater-mode sees the game, but there are no other displays to dim."
    elif games:
        headline = "theater-mode sees a game, but nothing is dimmed yet."
    else:
        headline = "theater-mode is idle, waiting for a Steam game."

    lines = [headline]
    for game in games:
        where = game.get("output") or "an unknown display"
        pending = "" if game.get("fullscreen") else " (not fullscreen)"
        lines.append(f"  Game:      AppID {game.get('appid')} on {where}{pending}")
    if dimmed:
        lines.append(f"  Dimmed:    {', '.join(dimmed)}")
    if outputs:
        lines.append(f"  Displays:  {', '.join(outputs)}")
    if dimmed and not data.get("effect_process_running"):
        lines.append("  The dimmer helper is not running. Run 'theater-mode doctor' for detail.")
    return "\n".join(lines)


_MISSING = object()


def _lookup(data: dict[str, Any], key: str) -> Any:
    """Read a dotted key path out of the resolved config JSON, or return _MISSING.

    Output ids may contain dots, so the schema-aware split is tried first and a plain
    dot split second (which is what addresses a whole table, e.g. 'effect').
    """
    candidates: list[list[str]] = []
    if (split := split_key_path(key)) is not None:
        table, leaf = split
        candidates.append(
            ["outputs", table.removeprefix("outputs."), leaf]
            if table.startswith("outputs.")
            else [table, leaf]
        )
    candidates.append(key.split("."))

    for path in candidates:
        value: Any = data
        for part in path:
            if not isinstance(value, dict) or part not in value:
                break
            value = value[part]
        else:
            return value
    return _MISSING


_TRUE_WORDS = frozenset({"true", "yes", "on", "1", "enable", "enabled"})
_FALSE_WORDS = frozenset({"false", "no", "off", "0", "disable", "disabled"})


def _parse_cli_value(val_str: str, key: str | None = None) -> Any:
    """Parse a scalar CLI value, consulting the schema when the key is known.

    A boolean key also accepts the everyday spellings of yes and no; a numeric key also
    accepts a trailing percent sign. Without a key the value is read by shape alone,
    which is what lets _display_value round-trip back through this function.

    Values the schema would refuse are returned unchanged for the daemon to reject, so
    the error names the key rather than the parse.
    """
    text = val_str.strip()
    lower = text.lower()
    type_name = spec.type_name if key and (spec := lookup_spec(key)) else None

    if type_name == "boolean":
        if lower in _TRUE_WORDS:
            return True
        if lower in _FALSE_WORDS:
            return False
    elif type_name == "float" and lower.endswith("%"):
        with contextlib.suppress(ValueError):
            return float(lower[:-1].strip()) / 100.0

    if lower == "true":
        return True
    if lower == "false":
        return False
    try:
        return float(text) if "." in text else int(text)
    except ValueError:
        return val_str


def _run_uninstaller(assume_yes: bool = False) -> int:
    """Hand off to the installer copy kept alongside the installed package."""
    installer = APP_DATA / "install.sh"
    if not installer.is_file():
        print(
            f"error: no uninstaller found at {installer}\n"
            "  This install predates 'theater-mode uninstall'. Run './install.sh --uninstall'\n"
            "  from the source tree you installed from.",
            file=sys.stderr,
        )
        return 1
    argv = [str(installer), "--uninstall"]
    if assume_yes:
        argv.append("--yes")
    return subprocess.run(argv, check=False).returncode  # noqa: S603


def main(
    argv: list[str] | None = None,
    call_dbus: Callable[..., str] = _call_dbus_method,
) -> int:
    parser = argparse.ArgumentParser(
        prog="theater-mode",
        description="Command-line client for theater-mode daemon and configuration management.",
    )
    parser.add_argument("--version", action="version", version=f"theater-mode {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    status_p = subparsers.add_parser("status", help="Show what theater-mode is doing now")
    status_p.add_argument(
        "--json", action="store_true", help="Output raw JSON instead of a summary"
    )

    config_parser = subparsers.add_parser("config", help="Manage daemon configuration")
    config_sub = config_parser.add_subparsers(dest="config_cmd", required=True)

    show_p = config_sub.add_parser("show", help="Display resolved configuration with provenance")
    show_p.add_argument("--json", action="store_true", help="Output raw JSON instead of table")

    diag_p = config_sub.add_parser("diagnostics", help="Display configuration warnings and errors")
    diag_p.add_argument("--json", action="store_true", help="Output raw JSON")

    get_p = config_sub.add_parser("get", help="Get a single resolved configuration value")
    get_p.add_argument("key", help="Key path (e.g. effect.dimming or outputs.DP-1.dimming)")

    set_p = config_sub.add_parser("set", help="Permanently commit setting to user config file")
    set_p.add_argument("key", help="Key path (e.g. effect.dimming)")
    set_p.add_argument("value", help="New value")

    unset_p = config_sub.add_parser(
        "unset", help="Remove a setting from the user config file, restoring its default"
    )
    unset_p.add_argument("keys", nargs="+", metavar="KEY", help="Key path(s) to remove")

    prev_p = config_sub.add_parser("preview", help="Preview setting in-session without saving")
    prev_p.add_argument("key", help="Key path (e.g. effect.dimming)")
    prev_p.add_argument("value", help="Preview value")

    config_sub.add_parser(
        "revert-preview", help="Discard session preview and revert to config file"
    )

    config_sub.add_parser("reload", help="Reload configuration files from disk")

    doc_p = subparsers.add_parser("doctor", help="Check this installation for problems")
    doc_p.add_argument("--json", action="store_true", help="Output findings as JSON")

    sim_p = subparsers.add_parser("simulate", help="Simulate a game launch")
    sim_p.add_argument("appid", help="Steam AppID")
    sim_p.add_argument("output", help="Target display connector (e.g. DP-1)")

    out_p = subparsers.add_parser(
        "outputs", help="List connected displays and the config keys that address them"
    )
    out_p.add_argument("--json", action="store_true", help="Output raw JSON")

    subparsers.add_parser(
        "clear", help="Immediately restore displays and clear tracked games or simulations"
    )

    uninstall_p = subparsers.add_parser("uninstall", help="Remove theater-mode from this machine")
    uninstall_p.add_argument(
        "-y", "--yes", action="store_true", help="Do not prompt for confirmation"
    )

    update_p = subparsers.add_parser("update", help="Update theater-mode to the latest release")
    update_p.add_argument(
        "--check",
        action="store_true",
        help="Report whether a newer release exists without installing it",
    )

    args = parser.parse_args(argv)

    if args.command == "update":
        from theater_mode import update as update_mod

        try:
            return update_mod.check() if args.check else update_mod.apply()
        except update_mod.UpdateError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

    if args.command == "uninstall":
        return _run_uninstaller(assume_yes=args.yes)

    if args.command == "doctor":
        from theater_mode import doctor

        checks = doctor.run_checks(call_dbus)
        print(doctor.to_json(checks) if args.json else doctor.format_report(checks))
        return doctor.exit_code(checks)

    match args.command, getattr(args, "config_cmd", None):
        case "status", _:
            raw = call_dbus("Status")
            print(raw if args.json else _format_status(json.loads(raw)))
        case "simulate", _:
            print(call_dbus("Simulate", args.appid, args.output))
        case "clear", _:
            print(call_dbus("Clear"))
        case "outputs", _:
            raw = call_dbus("GetOutputs")
            print(raw if args.json else _format_outputs(json.loads(raw)))

        case "config", "show" | "diagnostics" as sub:
            method = "GetResolved" if sub == "show" else "GetDiagnostics"
            raw = call_dbus(method)
            if args.json:
                print(raw)
            elif sub == "show":
                print(_format_provenance_table(json.loads(raw)))
            else:
                print(_format_diagnostics(json.loads(raw)))

        case "config", "get":
            value = _lookup(json.loads(call_dbus("GetResolved")), args.key)
            if value is _MISSING:
                print(
                    f"error: key '{args.key}' not found in resolved configuration",
                    file=sys.stderr,
                )
                return 1
            print(json.dumps(value, indent=2) if isinstance(value, dict) else _display_value(value))

        case "config", "set" | "preview" as sub:
            method = "Commit" if sub == "set" else "Preview"
            value = _parse_cli_value(args.value, args.key)
            result = call_dbus(method, json.dumps({args.key: value}))
            print(result)
            if result.startswith("error") or "rejected:" in result:
                return 1
            resolved = _lookup(json.loads(call_dbus("GetResolved")), args.key)
            if resolved is not _MISSING and not isinstance(resolved, dict):
                print(f"  {args.key} is now {_display_value(resolved)}")

        case "config", "unset":
            result = call_dbus("Unset", json.dumps(args.keys))
            print(result)
            if result.startswith("error") or "rejected:" in result:
                return 1
            resolved = json.loads(call_dbus("GetResolved"))
            for key in args.keys:
                value = _lookup(resolved, key)
                if value is not _MISSING and not isinstance(value, dict):
                    print(f"  {key} is now {_display_value(value)}")

        case "config", "revert-preview":
            print(call_dbus("RevertPreview"))
        case "config", "reload":
            print(call_dbus("Reload"))

    return 0


if __name__ == "__main__":
    sys.exit(main())
