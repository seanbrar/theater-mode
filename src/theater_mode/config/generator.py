"""Generate documented reference configuration from schema definitions."""

from __future__ import annotations

from theater_mode.config.schema import SCHEMA_TABLES, FieldSpec
from theater_mode.config.writer import format_toml_value

HEADER = """\
# theater-mode reference configuration
#
# Resolution hierarchy: Built-in defaults -> System config -> User config.
# User configuration path: ~/.config/theater-mode/config.toml
#
# To modify settings live via D-Bus without editing this file directly:
#   theater-mode config set effect.dim_factor 0.75
#   theater-mode config preview effect.mode log
#"""


def _document(field_name: str, spec: FieldSpec) -> list[str]:
    """Render one schema leaf as commented documentation plus its default assignment."""
    lines = [f"# {spec.doc}"]
    if spec.choices:
        lines.append("# Options: [" + ", ".join(f'"{c}"' for c in sorted(spec.choices)) + "]")

    bounds = [
        f"{label}: {value}"
        for label, value in (("min", spec.min_value), ("max", spec.max_value))
        if value is not None
    ]
    if bounds:
        lines.append(f"# Range: [{', '.join(bounds)}]")

    return [*lines, f"{field_name} = {format_toml_value(spec.default)}", ""]


def generate_reference_config() -> str:
    """Generate a fully-commented reference config.toml string from schema definitions."""
    lines = HEADER.splitlines()

    for table_name, table_fields in SCHEMA_TABLES.items():
        lines += ["", f"[{table_name}]"]
        for field_name, spec in table_fields.items():
            lines += _document(field_name, spec)

    # The per-output example is derived from the schema so it cannot drift from it.
    overridable = [
        name
        for fields in SCHEMA_TABLES.values()
        for name, spec in fields.items()
        if spec.allow_in_output
    ]
    lines += [
        "# " + "-" * 77,
        "# Per-Output Overrides (Optional)",
        "#",
        f"# Overridable keys: {', '.join(overridable)}",
        "#",
        "# Outputs are matched in this order, and the first section that exists wins:",
        "#   1. make:model:serial  - pins one physical panel, even among identical models",
        "#   2. make:model         - every panel of that model, stable across port swaps",
        "#   3. connector          - whatever is plugged into that port (e.g. DP-1)",
        "#",
        "# Run 'theater-mode outputs' to print the exact section headers for your displays.",
        "# " + "-" * 77,
        "#",
        '# [outputs."Dell Inc.:DELL S2721QS:4QCPZY3"]',
        "# dim_factor = 0.90",
        "#",
        '# [outputs."Dell Inc.:DELL S2721QS"]',
        "# art = false",
        "#",
        "# [outputs.DP-2]",
        "# dim_factor = 0.50",
        "# duration = 1.0",
        "#",
    ]

    return "\n".join(lines) + "\n"
