"""Atomic, format-preserving writer for user configuration files."""

from __future__ import annotations

import os
import re
import tempfile
import tomllib
from pathlib import Path
from typing import Any

from theater_mode.config.loader import (
    KEY_ASSIGN_PATTERN,
    OUTPUTS_PREFIX,
    TABLE_HEADER_PATTERN,
    get_default_user_path,
    normalize_table_path,
    split_key_path,
)

BARE_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_\-]+$")


def _quote(text: str) -> str:
    """Return text as a TOML basic string."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def format_toml_value(val: Any) -> str:
    """Format a Python value as a valid TOML literal."""
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, int | float):
        return str(val)
    if isinstance(val, str):
        return _quote(val)
    raise TypeError(f"Unsupported TOML value type: {type(val).__name__}")


def format_table_header(table: str) -> str:
    """Render a table path as a header, quoting an output id that is not a bare key.

    The id is emitted as a single quoted key rather than split on its dots: vendor names
    like 'Dell Inc.' contain dots that must not become table nesting.
    """
    if not table.startswith(OUTPUTS_PREFIX):
        return f"[{table}]"

    output_id = table[len(OUTPUTS_PREFIX) :]
    rendered = output_id if BARE_KEY_PATTERN.match(output_id) else _quote(output_id)
    return f"[{OUTPUTS_PREFIX}{rendered}]"


def _trailing_comment(text: str) -> str:
    """Return the trailing comment of a TOML value, ignoring '#' inside quoted strings."""
    quote = ""
    escaped = False
    for idx, char in enumerate(text):
        if escaped:
            escaped = False
        elif quote:
            escaped = char == "\\" and quote == '"'
            quote = "" if char == quote else quote
        elif char in "\"'":
            quote = char
        elif char == "#":
            return text[idx:].strip()
    return ""


def update_toml_content(original_content: str, updates: dict[str, Any]) -> str:
    """Update TOML text in place, preserving existing comments, whitespace, and ordering.

    updates maps a full key path ('effect.placement', 'outputs.DP-1.dim_factor') to a new value.
    """
    table_updates: dict[str, dict[str, str]] = {}
    for key_path, raw_val in updates.items():
        split = split_key_path(key_path)
        if split is None:
            raise ValueError(f"Malformed configuration key path: {key_path!r}")
        table, key = split
        table_updates.setdefault(table, {})[key] = format_toml_value(raw_val)

    new_lines: list[str] = []
    current_table = ""
    seen_keys: set[str] = set()
    written_tables: set[str] = set()

    def flush_pending() -> None:
        """Append keys destined for the table that is ending but absent from it."""
        pending = table_updates.get(current_table)
        if pending is None:
            return
        written_tables.add(current_table)
        # Insert above any trailing blank lines or comments so the new keys stay
        # attached to their own table rather than to whatever follows it.
        tail: list[str] = []
        while new_lines and (not new_lines[-1].strip() or new_lines[-1].lstrip().startswith("#")):
            tail.append(new_lines.pop())
        new_lines.extend(f"{k} = {pending[k]}" for k in sorted(set(pending) - seen_keys))
        new_lines.extend(reversed(tail))

    for line in original_content.splitlines():
        if table_match := TABLE_HEADER_PATTERN.match(line):
            flush_pending()
            current_table = normalize_table_path(table_match.group(1))
            seen_keys = set()
            new_lines.append(line)
            continue

        if key_match := KEY_ASSIGN_PATTERN.match(line):
            key_name = key_match.group(1).strip()
            seen_keys.add(key_name)

            if key_name in table_updates.get(current_table, {}):
                # Preserve indentation and any trailing comment.
                indent = line[: len(line) - len(line.lstrip())]
                comment = _trailing_comment(line.partition("=")[2])
                value = table_updates[current_table][key_name]
                new_lines.append(
                    f"{indent}{key_name} = {value}" + (f"  {comment}" if comment else "")
                )
                continue

        new_lines.append(line)

    flush_pending()

    for table in sorted(set(table_updates) - written_tables):
        if new_lines and new_lines[-1].strip():
            new_lines.append("")
        new_lines.append(format_table_header(table))
        new_lines.extend(f"{k} = {v}" for k, v in sorted(table_updates[table].items()))

    return "\n".join(new_lines).rstrip("\n") + "\n"


def remove_toml_keys(original_content: str, key_paths: set[str]) -> tuple[str, set[str]]:
    """Delete the given key paths from TOML text, preserving everything around them.

    Returns the new text and the subset of key paths that were actually present. A table
    left with no keys is kept: its header may carry comments the user wrote, and an empty
    table resolves identically to an absent one.
    """
    targets: dict[str, set[str]] = {}
    for key_path in key_paths:
        split = split_key_path(key_path)
        if split is None:
            raise ValueError(f"Malformed configuration key path: {key_path!r}")
        table, key = split
        targets.setdefault(table, set()).add(key)

    new_lines: list[str] = []
    removed: set[str] = set()
    current_table = ""

    for line in original_content.splitlines():
        if table_match := TABLE_HEADER_PATTERN.match(line):
            current_table = normalize_table_path(table_match.group(1))
            new_lines.append(line)
            continue

        if key_match := KEY_ASSIGN_PATTERN.match(line):
            key_name = key_match.group(1).strip()
            if key_name in targets.get(current_table, set()):
                removed.add(f"{current_table}.{key_name}")
                continue

        new_lines.append(line)

    return "\n".join(new_lines).rstrip("\n") + "\n", removed


def _write_atomically(target_path: Path, text: str) -> tuple[bool, str]:
    """Replace a config file in one step, so a crash cannot leave it half written."""
    temp_file: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=target_path.parent,
            prefix=f".{target_path.name}.tmp-",
            delete=False,
        ) as tf:
            temp_file = Path(tf.name)
            tf.write(text)
            tf.flush()
            os.fsync(tf.fileno())

        temp_file.replace(target_path)
        return True, f"Successfully committed config to {target_path}"
    except OSError as e:
        if temp_file is not None:
            temp_file.unlink(missing_ok=True)
        return False, f"Failed to write config file: {e}"


def unset_user_config(
    key_paths: set[str],
    user_config_path: Path | None = None,
) -> tuple[bool, str, set[str]]:
    """Remove keys from the user configuration file so they fall back to a lower layer.

    Returns (success, message, removed key paths).
    """
    target_path = user_config_path or get_default_user_path()
    if not target_path.is_file():
        return True, f"No user configuration file at {target_path}", set()

    try:
        original_text = target_path.read_text(encoding="utf-8")
    except OSError as e:
        return False, f"Failed to read existing config at {target_path}: {e}", set()

    updated_text, removed = remove_toml_keys(original_text, key_paths)
    if not removed:
        return True, f"No matching keys in {target_path}", set()

    try:
        tomllib.loads(updated_text)
    except tomllib.TOMLDecodeError as e:
        return False, f"Updated configuration contains invalid TOML syntax: {e}", set()

    ok, msg = _write_atomically(target_path, updated_text)
    return ok, msg, removed if ok else set()


def commit_user_config(
    updates: dict[str, Any],
    user_config_path: Path | None = None,
) -> tuple[bool, str]:
    """Atomically update or create the user configuration file with the given leaf updates.

    Returns (success, message).
    """
    target_path = user_config_path or get_default_user_path()
    try:
        target_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return False, f"Failed to create directory {target_path.parent}: {e}"

    original_text = ""
    if target_path.is_file():
        try:
            original_text = target_path.read_text(encoding="utf-8")
        except OSError as e:
            return False, f"Failed to read existing config at {target_path}: {e}"

    updated_text = update_toml_content(original_text, updates)
    try:
        tomllib.loads(updated_text)
    except tomllib.TOMLDecodeError as e:
        return False, f"Updated configuration contains invalid TOML syntax: {e}"

    return _write_atomically(target_path, updated_text)
