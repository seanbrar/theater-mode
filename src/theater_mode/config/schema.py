"""Configuration schema definitions, validation rules, and defaults."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from theater_mode.config.provenance import Layer, Provenance

VALID_EASING_CURVES: frozenset[str] = frozenset({"sine", "quad", "cubic", "linear"})
VALID_PLACEMENTS: frozenset[str] = frozenset({"over_windows", "behind_windows"})

DEFAULT_PLACEMENT = "over_windows"
DEFAULT_DIMMING = 0.85
DEFAULT_ARTWORK = True

DEFAULT_DURATION = 2.0
DEFAULT_CURVE = "sine"

DEFAULT_RESTORE_DELAY = 3.0
DEFAULT_FOLLOW_DELAY = 1.5
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


EFFECT_FIELDS: dict[str, FieldSpec] = {
    "placement": FieldSpec(
        key="placement",
        type_name="string",
        default=DEFAULT_PLACEMENT,
        choices=VALID_PLACEMENTS,
        doc="Where the effect sits: 'over_windows' covers whatever is on the display, blocking its light; 'behind_windows' paints on the desktop behind your open windows, which stay visible and usable.",
    ),
    "dimming": FieldSpec(
        key="dimming",
        type_name="float",
        default=DEFAULT_DIMMING,
        min_value=0.0,
        max_value=1.0,
        doc="How dark this display becomes: 0.0 leaves it untouched, 0.85 leaves 15% brightness, and 1.0 is solid black. With artwork enabled the value sets how darkly the artwork is drawn; with artwork off it sets how much of your desktop stays visible. Under placement 'behind_windows' it applies only behind your open windows, where a lower value usually reads better.",
    ),
    "artwork": FieldSpec(
        key="artwork",
        type_name="boolean",
        default=DEFAULT_ARTWORK,
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

BEHAVIOR_FIELDS: dict[str, FieldSpec] = {
    "restore_delay": FieldSpec(
        key="restore_delay",
        type_name="float",
        default=DEFAULT_RESTORE_DELAY,
        min_value=0.0,
        max_value=300.0,
        allow_in_output=False,
        doc="Grace period in seconds before restoring displays after all game windows close (0 disables delay).",
    ),
    "follow_delay": FieldSpec(
        key="follow_delay",
        type_name="float",
        default=DEFAULT_FOLLOW_DELAY,
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
    "behavior": BEHAVIOR_FIELDS,
}


@dataclass(frozen=True, slots=True)
class EffectConfig:
    """Resolved global effect settings."""

    placement: str = DEFAULT_PLACEMENT
    dimming: float = DEFAULT_DIMMING
    artwork: bool = DEFAULT_ARTWORK

    def to_dict(self) -> dict[str, Any]:
        return {
            "placement": self.placement,
            "dimming": self.dimming,
            "artwork": self.artwork,
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
class BehaviorConfig:
    """Resolved responses to a game window opening, moving, or closing."""

    restore_delay: float = DEFAULT_RESTORE_DELAY
    follow_delay: float = DEFAULT_FOLLOW_DELAY
    require_fullscreen: bool = DEFAULT_REQUIRE_FULLSCREEN

    def to_dict(self) -> dict[str, Any]:
        return {
            "restore_delay": self.restore_delay,
            "follow_delay": self.follow_delay,
            "require_fullscreen": self.require_fullscreen,
        }


@dataclass(frozen=True, slots=True)
class OutputOverrideConfig:
    """Per-output overrides for effect and transition leaves."""

    placement: str | None = None
    dimming: float | None = None
    artwork: bool | None = None
    duration: float | None = None
    curve: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            field: val
            for field in ("placement", "dimming", "artwork", "duration", "curve")
            if (val := getattr(self, field)) is not None
        }


@dataclass(frozen=True, slots=True)
class ResolvedDisplaySettings:
    """Fully resolved settings for a specific physical output."""

    output_id: str
    placement: str
    dimming: float
    artwork: bool
    duration: float
    curve: str
    matched_key: str | None = None


@dataclass(frozen=True, slots=True)
class ResolvedConfig:
    """Fully resolved, immutable configuration object with provenance tracking."""

    effect: EffectConfig = field(default_factory=EffectConfig)
    transition: TransitionConfig = field(default_factory=TransitionConfig)
    behavior: BehaviorConfig = field(default_factory=BehaviorConfig)
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
            dimming=(self.effect.dimming if override.dimming is None else override.dimming),
            artwork=self.effect.artwork if override.artwork is None else override.artwork,
            duration=(self.transition.duration if override.duration is None else override.duration),
            curve=self.transition.curve if override.curve is None else override.curve,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize resolved configuration and provenance for D-Bus / CLI."""
        return {
            "effect": self.effect.to_dict(),
            "transition": self.transition.to_dict(),
            "behavior": self.behavior.to_dict(),
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
