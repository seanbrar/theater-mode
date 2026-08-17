"""Display hardware, topology, and desktop containment interfaces."""

from theater_mode.display.base import OutputGeometry
from theater_mode.display.drm import connected_outputs
from theater_mode.display.kscreen import (
    output_brightness,
    output_geometries,
    output_positions,
    output_sizes,
    set_output_brightness,
)
from theater_mode.display.plasma import (
    output_desktop_map,
    plasma_evaluate,
    read_wallpapers,
    restore_wallpapers,
    write_wallpapers,
)

__all__ = [
    "OutputGeometry",
    "connected_outputs",
    "output_brightness",
    "output_geometries",
    "output_positions",
    "output_sizes",
    "set_output_brightness",
    "output_desktop_map",
    "plasma_evaluate",
    "read_wallpapers",
    "restore_wallpapers",
    "write_wallpapers",
]
