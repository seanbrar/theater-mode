"""Configuration management, resolution, and persistence for theater-mode."""

from theater_mode.config.dev import DevConfig, get_dev_config
from theater_mode.config.generator import generate_reference_config
from theater_mode.config.loader import (
    ConfigLoader,
    get_default_system_path,
    get_default_user_path,
    load_resolved_config,
    lookup_spec,
    split_key_path,
    system_config_dirs,
    validate_updates,
)
from theater_mode.config.provenance import Diagnostic, Layer, Provenance
from theater_mode.config.schema import (
    DaemonConfig,
    EffectConfig,
    OutputOverrideConfig,
    ResolvedConfig,
    ResolvedDisplaySettings,
    TransitionConfig,
)
from theater_mode.config.writer import (
    commit_user_config,
    format_table_header,
    remove_toml_keys,
    unset_user_config,
    update_toml_content,
)

__all__ = [
    "ConfigLoader",
    "DaemonConfig",
    "DevConfig",
    "Diagnostic",
    "EffectConfig",
    "Layer",
    "OutputOverrideConfig",
    "Provenance",
    "ResolvedConfig",
    "ResolvedDisplaySettings",
    "TransitionConfig",
    "commit_user_config",
    "remove_toml_keys",
    "format_table_header",
    "generate_reference_config",
    "get_default_system_path",
    "get_default_user_path",
    "get_dev_config",
    "load_resolved_config",
    "lookup_spec",
    "split_key_path",
    "system_config_dirs",
    "unset_user_config",
    "update_toml_content",
    "validate_updates",
]
