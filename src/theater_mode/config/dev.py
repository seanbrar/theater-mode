"""Development overrides and test fixture configuration read from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _parse_bool(val: str | None) -> bool:
    if val is None:
        return False
    return val.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class DevConfig:
    """Immutable collection of developer overrides and test fixtures."""

    user_config_override: Path | None = None
    verbose: bool = False
    force_art_dir: Path | None = None
    system_config_override: Path | None = None


def get_dev_config() -> DevConfig:
    """Read all Dev keys from the process environment in one centralized location."""
    user_override_raw = os.environ.get("THEATER_DEV_CONFIG_OVERRIDE")
    user_override = Path(user_override_raw).expanduser().resolve() if user_override_raw else None

    sys_override_raw = os.environ.get("THEATER_DEV_SYSTEM_CONFIG_OVERRIDE")
    sys_override = Path(sys_override_raw).expanduser().resolve() if sys_override_raw else None

    art_override_raw = os.environ.get("THEATER_DEV_FORCE_ART_DIR")
    art_override = Path(art_override_raw).expanduser().resolve() if art_override_raw else None

    return DevConfig(
        user_config_override=user_override,
        system_config_override=sys_override,
        force_art_dir=art_override,
        verbose=_parse_bool(os.environ.get("THEATER_DEV_VERBOSE")),
    )
