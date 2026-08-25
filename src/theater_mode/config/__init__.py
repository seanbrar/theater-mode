"""Configuration management, resolution, and persistence for theater-mode."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from theater_mode.config.dev import DevConfig, get_dev_config  # noqa: F401
    from theater_mode.config.generator import generate_reference_config  # noqa: F401
    from theater_mode.config.loader import (  # noqa: F401
        ConfigLoader,
        get_default_system_path,
        get_default_user_path,
        load_resolved_config,
        lookup_spec,
        split_key_path,
        system_config_dirs,
        validate_updates,
    )
    from theater_mode.config.provenance import Diagnostic, Layer, Provenance  # noqa: F401
    from theater_mode.config.schema import (  # noqa: F401
        BehaviorConfig,
        EffectConfig,
        OutputOverrideConfig,
        ResolvedConfig,
        ResolvedDisplaySettings,
        TransitionConfig,
    )
    from theater_mode.config.writer import (  # noqa: F401
        commit_user_config,
        format_table_header,
        remove_toml_keys,
        unset_user_config,
        update_toml_content,
    )

_EXPORT_MODULES: dict[str, str] = {
    "BehaviorConfig": "schema",
    "ConfigLoader": "loader",
    "DevConfig": "dev",
    "Diagnostic": "provenance",
    "EffectConfig": "schema",
    "Layer": "provenance",
    "OutputOverrideConfig": "schema",
    "Provenance": "provenance",
    "ResolvedConfig": "schema",
    "ResolvedDisplaySettings": "schema",
    "TransitionConfig": "schema",
    "commit_user_config": "writer",
    "format_table_header": "writer",
    "generate_reference_config": "generator",
    "get_default_system_path": "loader",
    "get_default_user_path": "loader",
    "get_dev_config": "dev",
    "load_resolved_config": "loader",
    "lookup_spec": "loader",
    "remove_toml_keys": "writer",
    "split_key_path": "loader",
    "system_config_dirs": "loader",
    "unset_user_config": "writer",
    "update_toml_content": "writer",
    "validate_updates": "loader",
}

__all__ = sorted(_EXPORT_MODULES)


def __getattr__(name: str) -> Any:
    """Lazily import and cache configuration symbols on first access."""
    submodule = _EXPORT_MODULES.get(name)
    if submodule is None:
        raise AttributeError(f"module '{__name__}' has no attribute '{name}'")
    module = importlib.import_module(f".{submodule}", __name__)
    value = getattr(module, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """List declared exports alongside standard module attributes."""
    return sorted(globals().keys() | set(__all__))
