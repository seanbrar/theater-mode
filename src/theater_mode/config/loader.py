"""Three-layer configuration loader, validation engine, and provenance tracker."""

from __future__ import annotations

import math
import os
import re
import tomllib
from functools import partial
from pathlib import Path
from typing import Any

from theater_mode.config.dev import DevConfig, get_dev_config
from theater_mode.config.provenance import Diagnostic, Layer, Provenance
from theater_mode.config.schema import (
    EFFECT_FIELDS,
    SCHEMA_TABLES,
    TRANSITION_FIELDS,
    BehaviorConfig,
    EffectConfig,
    FieldSpec,
    OutputOverrideConfig,
    ResolvedConfig,
    TransitionConfig,
    make_default_provenance,
)

TABLE_HEADER_PATTERN = re.compile(r"^\s*\[([^\[\]]+)\]\s*(?:#.*)?$")
KEY_ASSIGN_PATTERN = re.compile(r"^\s*([a-zA-Z0-9_\-]+)\s*=\s*(.*?)\s*(?:#.*)?$")
_TABLE_SEGMENT = re.compile(r"\"([^\"]*)\"|'([^']*)'|([^.]+)")


def normalize_table_path(raw: str) -> str:
    """Strip TOML quoting from a dotted table path: 'outputs."LG:27"' -> 'outputs.LG:27'."""
    segments = [
        next(group for group in match.groups() if group is not None).strip()
        for match in _TABLE_SEGMENT.finditer(raw.strip())
    ]
    return ".".join(segment for segment in segments if segment)


def system_config_dirs() -> list[Path]:
    """Return absolute paths from XDG_CONFIG_DIRS in preference order (defaults to /etc/xdg)."""
    raw = os.environ.get("XDG_CONFIG_DIRS", "")
    dirs = [candidate for entry in raw.split(":") if (candidate := Path(entry)).is_absolute()]
    return dirs or [Path("/etc/xdg")]


def get_default_system_path() -> Path:
    """Return the first existing system config in XDG_CONFIG_DIRS, falling back to the first candidate."""
    candidates = [directory / "theater-mode" / "config.toml" for directory in system_config_dirs()]
    return next((path for path in candidates if path.is_file()), candidates[0])


def get_default_user_path() -> Path:
    """Return the standard user configuration file path."""
    xdg_home = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg_home) if xdg_home else Path.home() / ".config"
    return base / "theater-mode" / "config.toml"


def output_field_spec(leaf_key: str) -> FieldSpec | None:
    """Return the spec for a leaf permitted inside [outputs.<id>], or None if disallowed."""
    spec = EFFECT_FIELDS.get(leaf_key) or TRANSITION_FIELDS.get(leaf_key)
    return spec if spec is not None and spec.allow_in_output else None


OUTPUTS_PREFIX = "outputs."


def split_key_path(key_path: str) -> tuple[str, str] | None:
    """Split a dotted key path into (table path, leaf key), or None if it is malformed.

    Output ids routinely contain dots of their own ('Dell Inc.:DELL S2721QS'), so an
    outputs.* path is split on its first and last separator rather than on every one.
    Leaf keys never contain dots, which makes that unambiguous. The id may also be
    quoted, matching how it is written as a TOML table header.
    """
    if key_path.startswith(OUTPUTS_PREFIX):
        output_id, separator, leaf = key_path[len(OUTPUTS_PREFIX) :].rpartition(".")
        if not separator or not output_id:
            return None
        return f"{OUTPUTS_PREFIX}{normalize_table_path(output_id)}", leaf

    table, separator, leaf = key_path.partition(".")
    if not separator or "." in leaf:
        return None
    return table, leaf


def lookup_spec(key_path: str) -> FieldSpec | None:
    """Return the spec addressed by a dotted key path, or None if no such key exists."""
    split = split_key_path(key_path)
    if split is None:
        return None

    table, leaf = split
    if table.startswith(OUTPUTS_PREFIX):
        return output_field_spec(leaf)
    return SCHEMA_TABLES[table].get(leaf) if table in SCHEMA_TABLES else None


def _extract_line_numbers(text: str) -> dict[str, int]:
    """Map key paths ('effect.placement', 'outputs.DP-1.dimming') to their line numbers."""
    line_map: dict[str, int] = {}
    current_table = ""

    for line_idx, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        if table_match := TABLE_HEADER_PATTERN.match(line):
            current_table = normalize_table_path(table_match.group(1))
            line_map[current_table] = line_idx
        elif key_match := KEY_ASSIGN_PATTERN.match(line):
            key_name = key_match.group(1).strip()
            line_map[f"{current_table}.{key_name}" if current_table else key_name] = line_idx

    return line_map


class ConfigLoader:
    """Three-layer configuration resolver with diagnostic collection."""

    def __init__(
        self,
        dev_config: DevConfig | None = None,
        session_overrides: dict[str, Any] | None = None,
    ) -> None:
        self.dev = dev_config or get_dev_config()
        self.session_overrides = session_overrides or {}
        self.diagnostics: list[Diagnostic] = []

    def _reject(
        self,
        spec: FieldSpec,
        value: Any,
        key_path: str,
        file_path: Path | None,
        line_num: int | None,
        message: str,
    ) -> None:
        """Record a validation failure; the built-in default stands in for the value."""
        self.diagnostics.append(
            Diagnostic(
                key_path=key_path,
                message=message,
                severity="error",
                source_file=file_path,
                line_number=line_num,
                offending_value=value,
                substituted_value=spec.default,
            )
        )

    def validate_leaf(
        self,
        spec: FieldSpec,
        value: Any,
        key_path: str,
        file_path: Path | None = None,
        line_num: int | None = None,
    ) -> Any | None:
        """Validate and coerce one leaf value, or return None after recording a diagnostic."""
        reject = partial(self._reject, spec, value, key_path, file_path, line_num)
        actual = type(value).__name__

        match spec.type_name:
            case "boolean":
                if not isinstance(value, bool):
                    return reject(f"Expected boolean (true/false) for '{key_path}', got {actual}")
                return value

            case "float":
                # bool is an int subclass, so reject it explicitly before the numeric check.
                if isinstance(value, bool) or not isinstance(value, int | float):
                    return reject(f"Expected number for '{key_path}', got {actual}")
                number = float(value)
                if not math.isfinite(number):
                    return reject(f"Value for '{key_path}' cannot be NaN or infinity")
                if spec.min_value is not None and number < spec.min_value:
                    return reject(
                        f"Value {number} for '{key_path}' is below minimum {spec.min_value}"
                    )
                if spec.max_value is not None and number > spec.max_value:
                    return reject(
                        f"Value {number} for '{key_path}' exceeds maximum {spec.max_value}"
                    )
                return number

            case "string":
                if not isinstance(value, str):
                    return reject(f"Expected string for '{key_path}', got {actual}")
                text = value.strip().lower()
                if spec.choices and text not in spec.choices:
                    allowed = ", ".join(sorted(spec.choices))
                    return reject(f"Invalid choice '{value}' for '{key_path}'. Allowed: {allowed}")
                return text

            case _:
                return value

    def _warn(
        self,
        key_path: str,
        message: str,
        path: Path | None,
        line_num: int | None,
        value: Any = None,
    ) -> None:
        """Record a structural problem that leaves the resolved value untouched."""
        self.diagnostics.append(
            Diagnostic(
                key_path=key_path,
                message=message,
                severity="warning",
                source_file=path,
                line_number=line_num,
                offending_value=value,
            )
        )

    def _load_file_layer(
        self, file_path: Path, layer: Layer
    ) -> tuple[dict[str, Any], dict[str, int]]:
        """Read and parse one TOML layer into (raw dict, key path -> line number)."""
        if not file_path.is_file():
            return {}, {}

        try:
            text = file_path.read_text(encoding="utf-8")
        except OSError as e:
            self.diagnostics.append(
                Diagnostic(
                    key_path="",
                    message=f"Failed to read {layer.value} config file: {e}",
                    source_file=file_path,
                )
            )
            return {}, {}

        try:
            raw_data = tomllib.loads(text)
        except tomllib.TOMLDecodeError as e:
            self.diagnostics.append(
                Diagnostic(
                    key_path="",
                    message=f"Malformed TOML in {layer.value} config: {e.args[0] if e.args else e}",
                    source_file=file_path,
                    line_number=getattr(e, "lineno", None),
                )
            )
            return {}, {}

        return raw_data, _extract_line_numbers(text)

    def _apply_outputs_section(
        self,
        section: Any,
        layer: Layer,
        path: Path,
        line_map: dict[str, int],
        outputs: dict[str, dict[str, Any]],
        provenance: dict[str, Provenance],
    ) -> None:
        """Merge an [outputs] section, one [outputs.<id>] table at a time."""
        if not isinstance(section, dict):
            self._warn(
                "outputs",
                f"[outputs] must be a table of output IDs, got {type(section).__name__}",
                path,
                line_map.get("outputs"),
            )
            return

        for output_id, table in section.items():
            if not isinstance(table, dict):
                self._warn(
                    f"outputs.{output_id}",
                    f"[outputs.{output_id}] must be a table, got {type(table).__name__}",
                    path,
                    line_map.get(f"outputs.{output_id}"),
                )
                continue

            target = outputs.setdefault(output_id, {})
            for leaf_key, leaf_val in table.items():
                key_path = f"outputs.{output_id}.{leaf_key}"
                line_num = line_map.get(key_path)

                spec = output_field_spec(leaf_key)
                if spec is None:
                    self._warn(
                        key_path,
                        f"Key '{leaf_key}' is not allowed in per-output table"
                        f" [outputs.{output_id}]",
                        path,
                        line_num,
                        leaf_val,
                    )
                    continue

                value = self.validate_leaf(spec, leaf_val, key_path, path, line_num)
                if value is not None:
                    target[leaf_key] = value
                    provenance[key_path] = Provenance(layer, path, line_num)

    def _apply_schema_section(
        self,
        name: str,
        section: Any,
        layer: Layer,
        path: Path,
        line_map: dict[str, int],
        tables: dict[str, dict[str, Any]],
        provenance: dict[str, Provenance],
    ) -> None:
        """Merge one known top-level table ([effect], [transition], [behavior])."""
        if not isinstance(section, dict):
            self._warn(
                name,
                f"[{name}] must be a table, got {type(section).__name__}",
                path,
                line_map.get(name),
            )
            return

        table_spec = SCHEMA_TABLES[name]
        for leaf_key, leaf_val in section.items():
            key_path = f"{name}.{leaf_key}"
            line_num = line_map.get(key_path)

            spec = table_spec.get(leaf_key)
            if spec is None:
                self._warn(
                    key_path, f"Unknown configuration key '{key_path}'", path, line_num, leaf_val
                )
                continue

            value = self.validate_leaf(spec, leaf_val, key_path, path, line_num)
            if value is not None:
                tables[name][leaf_key] = value
                provenance[key_path] = Provenance(layer, path, line_num)

    def _apply_session_overrides(
        self,
        tables: dict[str, dict[str, Any]],
        outputs: dict[str, dict[str, Any]],
        provenance: dict[str, Provenance],
    ) -> None:
        """Apply ephemeral in-memory preview values on top of the file layers."""
        for raw_key, raw_value in self.session_overrides.items():
            split = split_key_path(raw_key)
            spec = lookup_spec(raw_key)
            if split is None or spec is None:
                self._warn(raw_key, f"Unknown configuration key '{raw_key}'", None, None, raw_value)
                continue

            table, leaf = split
            key_path = f"{table}.{leaf}"

            value = self.validate_leaf(spec, raw_value, key_path)
            if value is None:
                continue

            if table.startswith(OUTPUTS_PREFIX):
                outputs.setdefault(table[len(OUTPUTS_PREFIX) :], {})[leaf] = value
            else:
                tables[table][leaf] = value
            provenance[key_path] = Provenance(layer=Layer.SESSION)

    def resolve(self) -> ResolvedConfig:
        """Execute full three-layer resolution and return an immutable ResolvedConfig."""
        self.diagnostics.clear()

        tables: dict[str, dict[str, Any]] = {
            name: {key: spec.default for key, spec in fields.items()}
            for name, fields in SCHEMA_TABLES.items()
        }
        outputs: dict[str, dict[str, Any]] = {}
        provenance = make_default_provenance()

        layers = (
            (Layer.SYSTEM, self.dev.system_config_override or get_default_system_path()),
            (Layer.USER, self.dev.user_config_override or get_default_user_path()),
        )

        for layer, path in layers:
            raw_data, line_map = self._load_file_layer(path, layer)

            for section_name, section in raw_data.items():
                if section_name == "outputs":
                    self._apply_outputs_section(section, layer, path, line_map, outputs, provenance)
                elif section_name in SCHEMA_TABLES:
                    self._apply_schema_section(
                        section_name, section, layer, path, line_map, tables, provenance
                    )
                else:
                    self._warn(
                        section_name,
                        f"Unknown configuration table '[{section_name}]'",
                        path,
                        line_map.get(section_name),
                    )

        self._apply_session_overrides(tables, outputs, provenance)

        return ResolvedConfig(
            effect=EffectConfig(**tables["effect"]),
            transition=TransitionConfig(**tables["transition"]),
            behavior=BehaviorConfig(**tables["behavior"]),
            outputs={k: OutputOverrideConfig(**v) for k, v in outputs.items()},
            provenance=provenance,
        )


def load_resolved_config(
    session_overrides: dict[str, Any] | None = None,
    dev_config: DevConfig | None = None,
) -> tuple[ResolvedConfig, list[Diagnostic]]:
    """Resolve configuration and return (config, diagnostics)."""
    loader = ConfigLoader(dev_config=dev_config, session_overrides=session_overrides)
    return loader.resolve(), loader.diagnostics


def validate_updates(updates: dict[str, Any]) -> tuple[dict[str, Any], list[Diagnostic]]:
    """Check a flat {key path: value} update set against the schema before it is written.

    Returns the accepted (coerced) subset plus diagnostics for everything rejected, so a
    caller never persists a key or value that the loader would refuse to read back.
    """
    loader = ConfigLoader(dev_config=DevConfig())
    accepted: dict[str, Any] = {}

    for key_path, value in updates.items():
        spec = lookup_spec(key_path)
        if spec is None:
            loader.diagnostics.append(
                Diagnostic(
                    key_path=key_path,
                    message=f"Unknown configuration key '{key_path}'",
                    severity="warning",
                    offending_value=value,
                )
            )
            continue
        coerced = loader.validate_leaf(spec, value, key_path)
        if coerced is not None:
            accepted[key_path] = coerced

    return accepted, loader.diagnostics
