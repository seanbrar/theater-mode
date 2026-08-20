"""Command-line client for theater-mode D-Bus control and configuration management."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Callable
from typing import Any

from theater_mode import __version__
from theater_mode.config import split_key_path
from theater_mode.constants import APP_DATA, BUS_NAME, INTERFACE, OBJECT_PATH


def _call_dbus_method(method_name: str, *args: Any) -> str:
    """Invoke a D-Bus method on the active theater-mode daemon and return the string response."""
    try:
        import gi

        gi.require_version("Gio", "2.0")
        gi.require_version("GLib", "2.0")
        from gi.repository import Gio, GLib

        conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        params = None
        if args:
            # Format arguments as tuple of strings
            params = GLib.Variant(f"({'s' * len(args)})", tuple(str(a) for a in args))

        result = conn.call_sync(
            BUS_NAME,
            OBJECT_PATH,
            INTERFACE,
            method_name,
            params,
            GLib.VariantType.new("(s)"),
            Gio.DBusCallFlags.NONE,
            5000,
            None,
        )
        return result.unpack()[0]
    except Exception as e:
        print(f"error: failed connecting to theater-moded daemon over D-Bus: {e}", file=sys.stderr)
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
        for section in ("effect", "transition", "daemon")
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

    lines: list[str] = [f"Found {len(diag_list)} configuration diagnostic(s):\n"]
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


def _parse_cli_value(val_str: str) -> Any:
    """Parse scalar CLI value into bool, float, int, or string."""
    lower = val_str.strip().lower()
    if lower == "true":
        return True
    if lower == "false":
        return False
    try:
        if "." in val_str:
            return float(val_str)
        return int(val_str)
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

    # status
    subparsers.add_parser("status", help="Show current daemon state and tracked windows")

    # config
    config_parser = subparsers.add_parser("config", help="Manage daemon configuration")
    config_sub = config_parser.add_subparsers(dest="config_cmd", required=True)

    # config show
    show_p = config_sub.add_parser("show", help="Display resolved configuration with provenance")
    show_p.add_argument("--json", action="store_true", help="Output raw JSON instead of table")

    # config diagnostics
    diag_p = config_sub.add_parser("diagnostics", help="Display configuration warnings and errors")
    diag_p.add_argument("--json", action="store_true", help="Output raw JSON")

    # config get
    get_p = config_sub.add_parser("get", help="Get a single resolved configuration value")
    get_p.add_argument("key", help="Key path (e.g. effect.dim_factor or outputs.DP-1.dim_factor)")

    # config set (Commit)
    set_p = config_sub.add_parser("set", help="Permanently commit setting to user config file")
    set_p.add_argument("key", help="Key path (e.g. effect.dim_factor)")
    set_p.add_argument("value", help="New value")

    # config unset
    unset_p = config_sub.add_parser(
        "unset", help="Remove a setting from the user config file, restoring its default"
    )
    unset_p.add_argument("keys", nargs="+", metavar="KEY", help="Key path(s) to remove")

    # config preview
    prev_p = config_sub.add_parser("preview", help="Preview setting in-session without saving")
    prev_p.add_argument("key", help="Key path (e.g. effect.dim_factor)")
    prev_p.add_argument("value", help="Preview value")

    # config revert-preview
    config_sub.add_parser(
        "revert-preview", help="Discard session preview and revert to config file"
    )

    # config reload
    config_sub.add_parser("reload", help="Reload configuration files from disk")

    # doctor
    doc_p = subparsers.add_parser("doctor", help="Check this installation for problems")
    doc_p.add_argument("--json", action="store_true", help="Output findings as JSON")

    # simulate
    sim_p = subparsers.add_parser("simulate", help="Simulate a game launch")
    sim_p.add_argument("appid", help="Steam AppID")
    sim_p.add_argument("output", help="Target display connector (e.g. DP-1)")

    # outputs
    out_p = subparsers.add_parser(
        "outputs", help="List connected displays and the config keys that address them"
    )
    out_p.add_argument("--json", action="store_true", help="Output raw JSON")

    # clear
    subparsers.add_parser("clear", help="Clear all active simulations and restore displays")

    # uninstall
    uninstall_p = subparsers.add_parser("uninstall", help="Remove theater-mode from this machine")
    uninstall_p.add_argument(
        "-y", "--yes", action="store_true", help="Do not prompt for confirmation"
    )

    # update
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
            print(call_dbus("Status"))
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
            result = call_dbus(method, json.dumps({args.key: _parse_cli_value(args.value)}))
            print(result)
            # The daemon reports refused keys inline rather than failing the D-Bus call.
            if result.startswith("error") or "rejected:" in result:
                return 1

        case "config", "unset":
            result = call_dbus("Unset", json.dumps(args.keys))
            print(result)
            if result.startswith("error") or "rejected:" in result:
                return 1
            # Show the resolved fallback value for each removed key.
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
