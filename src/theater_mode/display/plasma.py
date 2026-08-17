"""Plasma shell D-Bus bridge for screen containment mapping and wallpaper management."""

from __future__ import annotations

import json
import logging

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from theater_mode.constants import IMAGE_PLUGIN
from theater_mode.display.kscreen import output_positions

log = logging.getLogger("theater-moded")


def plasma_evaluate(script: str) -> str | None:
    """Execute a JavaScript snippet inside plasmashell via the evaluateScript D-Bus method.

    Per-screen wallpaper manipulation in KDE Plasma 6 requires evaluateScript because
    command-line tools like plasma-apply-wallpaperimage apply changes across all displays
    globally and containment configuration files cannot be modified directly while
    plasmashell is active.
    """
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        result = bus.call_sync(
            "org.kde.plasmashell",
            "/PlasmaShell",
            "org.kde.PlasmaShell",
            "evaluateScript",
            GLib.Variant("(s)", (script,)),
            GLib.VariantType("(s)"),
            Gio.DBusCallFlags.NONE,
            20000,
            None,
        )
        return result.unpack()[0]
    except GLib.Error as exc:
        log.error("Plasma evaluateScript call failed: %s", exc.message)
        return None


def output_desktop_map() -> dict[str, int]:
    """Map connector names (e.g., 'DP-1') to Plasma screen containment indices.

    Mapping Strategy:
    Displays are correlated using top-left (x, y) origin coordinates. Plasma reports
    logical desktop geometries while kscreen reports physical pixel coordinates, but both
    consistently share identical (x, y) origins.
    """
    script = (
        "var out = [];"
        "var ds = desktops();"
        "for (var i = 0; i < ds.length; i++) {"
        "  var g = screenGeometry(ds[i].screen);"
        "  out.push(ds[i].screen + ':' + g.x + ',' + g.y);"
        "}"
        "print(out.join(';'));"
    )
    printed = plasma_evaluate(script)
    if not printed:
        return {}

    by_position: dict[tuple[int, int], int] = {}
    for entry in printed.strip().split(";"):
        if ":" not in entry:
            continue
        index_str, _, coords = entry.partition(":")
        x_str, _, y_str = coords.partition(",")
        try:
            by_position[(int(x_str), int(y_str))] = int(index_str)
        except ValueError:
            continue

    positions = output_positions()
    return {name: by_position[pos] for name, pos in positions.items() if pos in by_position}


def read_wallpapers(screens: list[int]) -> dict[int, tuple[str, str]]:
    """Read current wallpaper plugin and image path for specified Plasma screen indices in one call."""
    if not screens:
        return {}

    screen_set = set(screens)
    script = (
        "var out = [];"
        "var ds = desktops();"
        "for (var i = 0; i < ds.length; i++) {"
        "  var plugin = ds[i].wallpaperPlugin;"
        f"  ds[i].currentConfigGroup = ['Wallpaper', {json.dumps(IMAGE_PLUGIN)}, 'General'];"
        "  out.push(ds[i].screen + '\\t' + plugin + '\\t' + ds[i].readConfig('Image'));"
        "}"
        "print(out.join(';'));"
    )
    printed = plasma_evaluate(script)
    if not printed:
        return {}

    results: dict[int, tuple[str, str]] = {}
    for entry in printed.strip().split(";"):
        if not entry:
            continue
        parts = entry.split("\t")
        if len(parts) >= 2:
            try:
                screen_idx = int(parts[0])
                if screen_idx in screen_set:
                    plugin = parts[1]
                    image_path = parts[2] if len(parts) > 2 else ""
                    results[screen_idx] = (plugin, image_path)
            except ValueError:
                continue

    return results


def write_wallpapers(changes: dict[int, str]) -> None:
    """Set custom wallpaper images for designated screen indices."""
    if not changes:
        return

    lines = ["var ds = desktops();", "for (var i = 0; i < ds.length; i++) {"]
    for screen, path in changes.items():
        lines.append(
            f"  if (ds[i].screen === {screen}) {{"
            f"    ds[i].wallpaperPlugin = {json.dumps(IMAGE_PLUGIN)};"
            f"    ds[i].currentConfigGroup = ['Wallpaper', {json.dumps(IMAGE_PLUGIN)}, 'General'];"
            f"    ds[i].writeConfig('Image', {json.dumps('file://' + path)});"
            f"    ds[i].writeConfig('FillMode', 2);"
            "    ds[i].reloadConfig();"
            "  }"
        )
    lines.append("}")
    plasma_evaluate("".join(lines))


def restore_wallpapers(restore: dict[int, tuple[str, str]]) -> None:
    """Restore original wallpaper plugin and configuration for designated screen indices."""
    if not restore:
        return

    lines = ["var ds = desktops();", "for (var i = 0; i < ds.length; i++) {"]
    for screen, (plugin, image) in restore.items():
        if not plugin:
            continue
        lines.append(
            f"  if (ds[i].screen === {screen}) {{"
            f"    ds[i].currentConfigGroup = ['Wallpaper', {json.dumps(IMAGE_PLUGIN)}, 'General'];"
            f"    ds[i].writeConfig('Image', {json.dumps(image)});"
            f"    ds[i].wallpaperPlugin = {json.dumps(plugin)};"
            "    ds[i].reloadConfig();"
            "  }"
        )
    lines.append("}")
    plasma_evaluate("".join(lines))
