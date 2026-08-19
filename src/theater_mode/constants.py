"""Shared constants, D-Bus interface definitions, and system paths."""

from __future__ import annotations

import os
import re
from pathlib import Path

# D-Bus Registration
BUS_NAME = "org.theatermode.TheaterMode"
OBJECT_PATH = "/org/theatermode/TheaterMode"
INTERFACE = "org.theatermode.TheaterMode"


def _xdg_dir(variable: str, fallback: str) -> Path:
    """Resolve an XDG base directory. An empty value counts as unset, as the spec requires."""
    return Path(os.environ.get(variable) or Path.home() / fallback)


# Installed Layout
APP_DATA = _xdg_dir("XDG_DATA_HOME", ".local/share") / "theater-mode"

# Steam & Artwork Cache Paths
STEAM_LIBRARY_CACHE = (
    _xdg_dir("XDG_DATA_HOME", ".local/share") / "Steam" / "appcache" / "librarycache"
)
FLATPAK_STEAM_LIBRARY_CACHE = (
    Path.home()
    / ".var"
    / "app"
    / "com.valvesoftware.Steam"
    / "data"
    / "Steam"
    / "appcache"
    / "librarycache"
)
FLATPAK_STEAM_XDG_LIBRARY_CACHE = (
    Path.home()
    / ".var"
    / "app"
    / "com.valvesoftware.Steam"
    / ".local"
    / "share"
    / "Steam"
    / "appcache"
    / "librarycache"
)
STEAM_LIBRARY_CACHES = (
    STEAM_LIBRARY_CACHE,
    FLATPAK_STEAM_LIBRARY_CACHE,
    FLATPAK_STEAM_XDG_LIBRARY_CACHE,
)
ART_CACHE = _xdg_dir("XDG_CACHE_HOME", ".cache") / "theater-mode"

# Helper Binaries
DIMMER_BINARY_NAME = "theater-dimmer"
ART_BINARY_NAME = "theater-art"

# Session integration. These mirror install.sh: the KWin script is installed as a package
# directory and switched on through the same kwinrc key System Settings writes.
KWIN_PLUGIN_ID = "theater-detect"
KWIN_SCRIPT_DIR = _xdg_dir("XDG_DATA_HOME", ".local/share") / "kwin" / "scripts" / KWIN_PLUGIN_ID
KWIN_CONFIG_FILE = _xdg_dir("XDG_CONFIG_HOME", ".config") / "kwinrc"
SERVICE_UNIT = "theater-mode.service"

# Release distribution
PROJECT_REPO = "seanbrar/theater-mode"
RELEASE_API = f"https://api.github.com/repos/{PROJECT_REPO}/releases/latest"

# Steam Detection Patterns
STEAM_APP_CLASS = re.compile(r"^steam_app_(\d+)$")
STEAM_LAUNCH_ARG = re.compile(r"\bAppId=(\d+)\b")

# Process classes excluded from game detection to prevent false positives from
# client surfaces, overlays, or desktop shells that inherit Steam environment variables.
IGNORED_CLASSES = frozenset(
    {
        "steam",
        "steamwebhelper",
        "steamdeckgyrodsu",
        "plasmashell",
        "xwaylandvideobridge",
        "org.kde.plasmashell",
    }
)

# KWin Script D-Bus Interface Definition
#
# NOTE: Arguments from KWin's script engine are passed as strings (including booleans
# and numeric IDs) because KWin's callDBus dynamically infers types and cannot reliably
# produce uint32/boolean primitives without bus-level signature mismatch rejections.
# Type parsing and normalization are performed explicitly inside the daemon.
INTERFACE_XML = f"""
<node>
  <interface name='{INTERFACE}'>
    <method name='WindowOpened'>
      <arg type='s' name='window_id' direction='in'/>
      <arg type='s' name='resource_class' direction='in'/>
      <arg type='s' name='pid' direction='in'/>
      <arg type='s' name='output' direction='in'/>
      <arg type='s' name='fullscreen' direction='in'/>
      <arg type='s' name='normal' direction='in'/>
    </method>
    <method name='WindowChanged'>
      <arg type='s' name='window_id' direction='in'/>
      <arg type='s' name='output' direction='in'/>
      <arg type='s' name='fullscreen' direction='in'/>
    </method>
    <method name='WindowClosed'>
      <arg type='s' name='window_id' direction='in'/>
    </method>
    <method name='SnapshotBegin'/>
    <method name='SnapshotEnd'/>
    <method name='Status'>
      <arg type='s' name='status_json' direction='out'/>
    </method>
    <method name='Simulate'>
      <arg type='s' name='appid' direction='in'/>
      <arg type='s' name='output' direction='in'/>
      <arg type='s' name='result' direction='out'/>
    </method>
    <method name='Clear'>
      <arg type='s' name='result' direction='out'/>
    </method>
    <method name='GetOutputs'>
      <arg type='s' name='outputs_json' direction='out'/>
    </method>
    <method name='GetResolved'>
      <arg type='s' name='config_json' direction='out'/>
    </method>
    <method name='GetDiagnostics'>
      <arg type='s' name='diagnostics_json' direction='out'/>
    </method>
    <method name='Preview'>
      <arg type='s' name='keys_json' direction='in'/>
      <arg type='s' name='result' direction='out'/>
    </method>
    <method name='RevertPreview'>
      <arg type='s' name='result' direction='out'/>
    </method>
    <method name='Commit'>
      <arg type='s' name='keys_json' direction='in'/>
      <arg type='s' name='result' direction='out'/>
    </method>
    <method name='Reload'>
      <arg type='s' name='result' direction='out'/>
    </method>
    <signal name='ConfigChanged'/>
  </interface>
</node>
"""
