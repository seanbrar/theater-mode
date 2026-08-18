"""Shared constants, D-Bus interface definitions, and system paths."""

from __future__ import annotations

import os
import re
from pathlib import Path

# D-Bus Registration
BUS_NAME = "org.theatermode.TheaterMode"
OBJECT_PATH = "/org/theatermode/TheaterMode"
INTERFACE = "org.theatermode.TheaterMode"

# Steam & Artwork Cache Paths
STEAM_LIBRARY_CACHE = (
    Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    / "Steam"
    / "appcache"
    / "librarycache"
)
ART_CACHE = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "theater-mode"

# Wayland Dimmer Defaults
DEFAULT_DIM_FACTOR = 0.85
DEFAULT_DIM_DURATION = 2.0
DEFAULT_DIM_CURVE = "sine"
DIMMER_BINARY_NAME = "theater-dimmer"

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
  </interface>
</node>
"""
