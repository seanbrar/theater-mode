"""theater_mode — Smart multi-monitor theater mode for KDE Plasma on Wayland."""

import glob
import importlib
import sys

# Ensure PyGObject is discoverable in containerized/sandbox developer environments
try:
    import gi

    if not hasattr(gi, "require_version"):
        for path in glob.glob("/run/host/usr/lib64/python3.*/site-packages") + glob.glob(
            "/run/host/usr/lib/python3.*/site-packages"
        ):
            if path not in sys.path:
                sys.path.insert(0, path)
        importlib.reload(gi)
except ImportError:
    pass

__version__ = "1.0.0"
