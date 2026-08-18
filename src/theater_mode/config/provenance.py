"""Provenance and diagnostic data models for theater-mode configuration."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal


class Layer(StrEnum):
    """Configuration resolution layer ordered from lowest to highest precedence."""

    BUILTIN = "builtin"
    SYSTEM = "system"
    USER = "user"
    SESSION = "session"


@dataclass(frozen=True, slots=True)
class Provenance:
    """Provenance metadata tracking the origin of a resolved configuration value."""

    layer: Layer
    file_path: Path | None = None
    line_number: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "layer": self.layer.value,
            "file": str(self.file_path) if self.file_path else None,
            "line": self.line_number,
        }


type Severity = Literal["error", "warning", "info"]


@dataclass(frozen=True, slots=True)
class Diagnostic:
    """Structured diagnostic record for an invalid, unknown, or malformed configuration entry."""

    key_path: str
    message: str
    severity: Severity = "error"
    source_file: Path | None = None
    line_number: int | None = None
    offending_value: Any = None
    substituted_value: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "key_path": self.key_path,
            "message": self.message,
            "severity": self.severity,
            "file": str(self.source_file) if self.source_file else None,
            "line": self.line_number,
            "offending_value": self.offending_value,
            "substituted_value": self.substituted_value,
        }
