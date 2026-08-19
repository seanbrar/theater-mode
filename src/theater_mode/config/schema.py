"""Configuration schema definitions, validation rules, and defaults."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from theater_mode.config.provenance import Layer, Provenance

# Allowed choices
VALID_EFFECT_MODES: frozenset[str] = frozenset({"dim", "log"})
VALID_EASING_CURVES: frozenset[str] = frozenset({"sine", "quad", "cubic", "linear"})
VALID_PLACEMENTS: frozenset[str] = frozenset({"over_windows", "behind_windows"})

# Defaults
DEFAULT_EFFECT_MODE = "dim"
DEFAULT_PLACEMENT = "over_windows"
DEFAULT_DIM_FACTOR = 0.85
DEFAULT_ART = True

DEFAULT_DURATION = 2.0
DEFAULT_CURVE = "sine"

DEFAULT_REVERT_DELAY = 3.0
DEFAULT_STAGE_DELAY = 1.5
DEFAULT_REQUIRE_FULLSCREEN = False


@dataclass(frozen=True, slots=True)
class FieldSpec:
    """Metadata specification for a configuration leaf key."""

    key: str
    type_name: str
    default: Any
    doc: str
    choices: frozenset[str] | None = None
    min_value: float | None = None
    max_value: float | None = None
    allow_in_output: bool = True


# Schema field declarations
EFFECT_FIELDS: dict[str, FieldSpec] = {
    "mode": FieldSpec(
        key="mode",
        type_name="string",
        default=DEFAULT_EFFECT_MODE,
        choices=VALID_EFFECT_MODES,
        allow_in_output=False,
        doc="Display effect to apply to secondary outputs: 'dim' (cinematic overlay) or 'log' (dry run).",
    ),
    "placement": FieldSpec(
        key="placement",
        type_name="string",
        default=DEFAULT_PLACEMENT,
        choices=VALID_PLACEMENTS,
        doc="Where the effect sits: 'over_windows' covers whatever is on the display, blocking its light; 'behind_windows' paints on the desktop behind your open windows, which stay visible and usable.",
    ),
    "dim_factor": FieldSpec(
        key="dim_factor",
        type_name="float",
        default=DEFAULT_DIM_FACTOR,
        min_value=0.0,
        max_value=1.0,
        doc="Fraction of brightness to remove (0.0 = untouched, 1.0 = solid black, 0.85 = 15% brightness). With placement 'over_windows' this dims everything on the display; with 'behind_windows' it only darkens what is drawn behind your windows, where a much lower value usually reads better.",
    ),
    "art": FieldSpec(
        key="art",
        type_name="boolean",
        default=DEFAULT_ART,
        doc="Show active Steam game library hero artwork on secondary displays (true) or flat dark color (false).",
    ),
}

TRANSITION_FIELDS: dict[str, FieldSpec] = {
    "duration": FieldSpec(
        key="duration",
        type_name="float",
        default=DEFAULT_DURATION,
        min_value=0.01,
        max_value=60.0,
        doc="Transition fade duration in seconds.",
    ),
    "curve": FieldSpec(
        key="curve",
        type_name="string",
        default=DEFAULT_CURVE,
        choices=VALID_EASING_CURVES,
        doc="Mathematical easing curve for fade transitions: 'sine', 'quad', 'cubic', or 'linear'.",
    ),
}

DAEMON_FIELDS: dict[str, FieldSpec] = {
    "revert_delay": FieldSpec(
        key="revert_delay",
        type_name="float",
        default=DEFAULT_REVERT_DELAY,
        min_value=0.0,
        max_value=300.0,
        allow_in_output=False,
        doc="Grace period in seconds before restoring displays after all game windows close (0 disables delay).",
    ),
    "stage_delay": FieldSpec(
        key="stage_delay",
        type_name="float",
        default=DEFAULT_STAGE_DELAY,
        min_value=0.0,
        max_value=60.0,
        allow_in_output=False,
        doc="Stability delay in seconds before following a game window when it moves to a different display.",
    ),
    "require_fullscreen": FieldSpec(
        key="require_fullscreen",
        type_name="boolean",
        default=DEFAULT_REQUIRE_FULLSCREEN,
        allow_in_output=False,
        doc="Only activate theater mode when the game window enters true fullscreen.",
    ),
}

SCHEMA_TABLES: dict[str, dict[str, FieldSpec]] = {
    "effect": EFFECT_FIELDS,
    "transition": TRANSITION_FIELDS,
    "daemon": DAEMON_FIELDS,
}


@dataclass(frozen=True, slots=True)
class EffectConfig:
    """Resolved global effect settings."""

    mode: str = DEFAULT_EFFECT_MODE
    placement: str = DEFAULT_PLACEMENT
    dim_factor: float = DEFAULT_DIM_FACTOR
    art: bool = DEFAULT_ART

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "placement": self.placement,
            "dim_factor": self.dim_factor,
            "art": self.art,
        }


@dataclass(frozen=True, slots=True)
class TransitionConfig:
    """Resolved global transition timing and easing settings."""

    duration: float = DEFAULT_DURATION
    curve: str = DEFAULT_CURVE

    def to_dict(self) -> dict[str, Any]:
        return {
            "duration": self.duration,
            "curve": self.curve,
        }


@dataclass(frozen=True, slots=True)
class DaemonConfig:
    """Resolved daemon lifecycle and game detection settings."""

    revert_delay: float = DEFAULT_REVERT_DELAY
    stage_delay: float = DEFAULT_STAGE_DELAY
    require_fullscreen: bool = DEFAULT_REQUIRE_FULLSCREEN

    def to_dict(self) -> dict[str, Any]:
        return {
            "revert_delay": self.revert_delay,
            "stage_delay": self.stage_delay,
            "require_fullscreen": self.require_fullscreen,
        }


@dataclass(frozen=True, slots=True)
class OutputOverrideConfig:
    """Per-output overrides for effect and transition leaves."""

    placement: str | None = None
    dim_factor: float | None = None
    art: bool | None = None
    duration: float | None = None
    curve: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            field: val
            for field in ("placement", "dim_factor", "art", "duration", "curve")
            if (val := getattr(self, field)) is not None
        }


@dataclass(frozen=True, slots=True)
class ResolvedDisplaySettings:
    """Fully resolved settings for a specific physical output."""

    output_id: str
    placement: str
    dim_factor: float
    art: bool
    duration: float
    curve: str
    matched_key: str | None = None


@dataclass(frozen=True, slots=True)
class ResolvedConfig:
    """Fully resolved, immutable configuration object with provenance tracking."""

    effect: EffectConfig = field(default_factory=EffectConfig)
    transition: TransitionConfig = field(default_factory=TransitionConfig)
    daemon: DaemonConfig = field(default_factory=DaemonConfig)
    outputs: dict[str, OutputOverrideConfig] = field(default_factory=dict)
    provenance: dict[str, Provenance] = field(default_factory=dict)

    def resolve_for_output(
        self,
        output_name: str,
        match_keys: Sequence[str] = (),
    ) -> ResolvedDisplaySettings:
        """Resolve effective settings for one output, applying the per-output match hierarchy.

        match_keys are the output's EDID identifiers, most specific first (see
        OutputIdentity.match_keys); they are tried before the connector name:

        1. make:model:serial (e.g. 'Dell Inc.:DELL S2721QS:4QCPZY3')
        2. make:model (e.g. 'Dell Inc.:DELL S2721QS')
        3. connector name (e.g. 'DP-1')
        4. global resolved defaults
        """
        matched_key = next((k for k in (*match_keys, output_name) if k in self.outputs), None)
        override = self.outputs.get(matched_key) if matched_key else None
        override = override or OutputOverrideConfig()

        return ResolvedDisplaySettings(
            output_id=output_name,
            matched_key=matched_key,
            placement=(self.effect.placement if override.placement is None else override.placement),
            dim_factor=(
                self.effect.dim_factor if override.dim_factor is None else override.dim_factor
            ),
            art=self.effect.art if override.art is None else override.art,
            duration=(self.transition.duration if override.duration is None else override.duration),
            curve=self.transition.curve if override.curve is None else override.curve,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize resolved configuration and provenance for D-Bus / CLI."""
        return {
            "effect": self.effect.to_dict(),
            "transition": self.transition.to_dict(),
            "daemon": self.daemon.to_dict(),
            "outputs": {k: v.to_dict() for k, v in self.outputs.items()},
            "provenance": {k: v.to_dict() for k, v in self.provenance.items()},
        }


def make_default_provenance() -> dict[str, Provenance]:
    """Generate default provenance mapping for all schema keys."""
    prov: dict[str, Provenance] = {}
    for table_name, table_spec in SCHEMA_TABLES.items():
        for field_name in table_spec:
            prov[f"{table_name}.{field_name}"] = Provenance(layer=Layer.BUILTIN)
    return prov
