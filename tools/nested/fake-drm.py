#!/usr/bin/env python3
"""Build a synthetic /sys/class/drm tree from a display profile.

`display/drm.py` reads connector state from sysfs, which is global to the machine and
therefore describes the *host's* monitors even when the daemon is talking to a nested
compositor. Bind-mounting a synthetic tree over /sys/class/drm lets the daemon see the
outputs the nested compositor actually advertises.

The tree is also the cheapest way to reach EDID cases no real desk can produce: a display
whose EDID is absent, truncated, or fails its checksum, a vendor whose PnP code is not in
hwdata, or four monitors on a machine with one. Those paths are documented as
fault-tolerant in `.local/docs/architecture.md` §6; this is how they get exercised.

Only the fields `display/edid.py` and `display/drm.py` actually read are synthesized:
the header, the packed manufacturer ID, the product code and serial number, the monitor
name and serial descriptors, and the checksum byte.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import struct
import sys
from pathlib import Path
from typing import Any

EDID_HEADER = b"\x00\xff\xff\xff\xff\xff\xff\x00"
EDID_BLOCK_SIZE = 128

# Display descriptors live at bytes 54..125 in four 18-byte slots.
DESCRIPTOR_START = 54
DESCRIPTOR_SIZE = 18
TAG_SERIAL = 0xFF
TAG_NAME = 0xFC
OUTPUT_KEYS = {
    "connector",
    "corrupt",
    "edid",
    "model",
    "modes",
    "pnp_id",
    "product_code",
    "serial",
    "serial_number",
    "status",
}


def pack_pnp_id(code: str) -> bytes:
    """Pack a 3-letter PnP vendor code into its two-byte EDID representation."""
    if re.fullmatch(r"[A-Za-z]{3}", code) is None:
        raise ValueError(f"PnP id must be three letters, got {code!r}")
    packed = 0
    for letter in code.upper():
        packed = (packed << 5) | (ord(letter) - ord("A") + 1)
    return struct.pack(">H", packed)


def text_descriptor(tag: int, text: str) -> bytes:
    """Build one 18-byte display descriptor carrying an ASCII string."""
    # A zero pixel clock in the first two bytes is what marks a slot as a display
    # descriptor rather than a detailed timing block.
    body = text.encode("ascii", errors="replace")[:13]
    if len(body) < 13:
        body += b"\n" + b" " * (12 - len(body))
    return bytes([0x00, 0x00, 0x00, tag, 0x00]) + body


def build_edid(
    pnp_id: str,
    model: str,
    serial: str | None,
    product_code: int,
    serial_number: int,
) -> bytearray:
    """Assemble a checksum-valid 128-byte EDID base block."""
    block = bytearray(EDID_BLOCK_SIZE)
    block[0:8] = EDID_HEADER
    block[8:10] = pack_pnp_id(pnp_id)
    block[10:16] = struct.pack("<HI", product_code, serial_number)
    block[16] = 1  # Manufacture week.
    block[17] = 36  # Manufacture year, offset from 1990 (2026).
    block[18] = 1  # EDID version 1.
    block[19] = 4  # EDID revision 4.

    descriptors = [text_descriptor(TAG_NAME, model)]
    if serial:
        descriptors.append(text_descriptor(TAG_SERIAL, serial))
    for index, descriptor in enumerate(descriptors):
        start = DESCRIPTOR_START + index * DESCRIPTOR_SIZE
        block[start : start + DESCRIPTOR_SIZE] = descriptor

    block[127] = (256 - sum(block[:127]) % 256) % 256
    return block


def corrupt(block: bytearray, mode: str) -> bytes:
    """Damage an EDID blob in one of the ways a real display plausibly would."""
    if mode == "checksum":
        # A KVM switch mid-handshake returns a block whose checksum no longer holds.
        block[127] ^= 0xFF
        return bytes(block)
    if mode == "truncated":
        # A sleeping display often returns a short read.
        return bytes(block[:64])
    if mode == "header":
        # A blank or all-zero read from a disconnected sink.
        return bytes(EDID_BLOCK_SIZE)
    raise ValueError(f"unknown corruption mode {mode!r}")


def rename_connector(connector: str, prefix: str | None) -> str:
    """Re-prefix a connector name, keeping its ordinal.

    KWin names windowed outputs WL-0, WL-1, ... and virtual-framebuffer outputs
    Virtual-0, Virtual-1, ... A profile describes displays, not a backend, so the
    caller supplies whichever prefix the compositor it is about to start will use.
    """
    if not prefix:
        return connector
    return prefix + connector.rpartition("-")[2]


def validate_output(spec: Any, index: int) -> dict[str, Any]:
    """Validate one profile entry and return it with a precise failure location."""
    if not isinstance(spec, dict):
        raise ValueError(f"outputs[{index}] must be an object")
    unknown = sorted(set(spec) - OUTPUT_KEYS)
    if unknown:
        raise ValueError(f"outputs[{index}] has unknown field {unknown[0]!r}")
    connector = spec.get("connector")
    if not isinstance(connector, str) or re.fullmatch(r"[A-Za-z0-9_.:-]+", connector) is None:
        raise ValueError(f"outputs[{index}].connector must be a non-empty connector name")
    pnp_id = spec.get("pnp_id", "XXX")
    if not isinstance(pnp_id, str) or re.fullmatch(r"[A-Za-z]{3}", pnp_id) is None:
        raise ValueError(f"outputs[{index}].pnp_id must be three ASCII letters")
    if "model" in spec and not isinstance(spec["model"], str):
        raise ValueError(f"outputs[{index}].model must be a string")
    if "serial" in spec and spec["serial"] is not None and not isinstance(spec["serial"], str):
        raise ValueError(f"outputs[{index}].serial must be a string or null")
    if "status" in spec and spec["status"] not in ("connected", "disconnected", "unknown"):
        raise ValueError(
            f"outputs[{index}].status must be 'connected', 'disconnected', or 'unknown'"
        )
    if "edid" in spec and not isinstance(spec["edid"], bool):
        raise ValueError(f"outputs[{index}].edid must be a boolean")
    if spec.get("corrupt") not in (None, "checksum", "truncated", "header"):
        raise ValueError(f"outputs[{index}].corrupt has an unknown mode")
    for key in ("product_code", "serial_number"):
        value = spec.get(key, 0)
        limit = 0xFFFF if key == "product_code" else 0xFFFFFFFF
        if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= limit:
            raise ValueError(f"outputs[{index}].{key} must be an integer from 0 to {limit}")
    modes = spec.get("modes", [])
    if not isinstance(modes, list) or not all(
        isinstance(mode, str) and re.fullmatch(r"[1-9][0-9]*x[1-9][0-9]*", mode) is not None
        for mode in modes
    ):
        raise ValueError(f"outputs[{index}].modes must be a list of WxH geometry strings")
    return spec


def load_profile(path: Path) -> list[dict[str, Any]]:
    """Load and validate the output list from a display profile."""
    try:
        profile = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"could not read profile {path}: {error}") from error
    if not isinstance(profile, dict):
        raise ValueError("profile root must be an object")
    unknown = sorted(set(profile) - {"description", "outputs"})
    if unknown:
        raise ValueError(f"profile has unknown field {unknown[0]!r}")
    if "description" in profile and not isinstance(profile["description"], str):
        raise ValueError("profile description must be a string")
    outputs = profile.get("outputs")
    if not isinstance(outputs, list) or not outputs:
        raise ValueError(f"{path} defines no outputs")
    return [validate_output(spec, index) for index, spec in enumerate(outputs)]


def write_output(root: Path, card: str, spec: dict[str, Any], prefix: str | None = None) -> str:
    """Materialize one connector directory and return its connector name."""
    connector = rename_connector(spec["connector"], prefix)
    if re.fullmatch(r"card[0-9]+", card) is None:
        raise ValueError("card must have the form cardN")
    if re.fullmatch(r"[A-Za-z0-9_.:-]+", connector) is None:
        raise ValueError(f"invalid generated connector name {connector!r}")
    path = root / f"{card}-{connector}"
    path.mkdir(parents=True)

    (path / "status").write_text(f"{spec.get('status', 'connected')}\n", encoding="utf-8")
    (path / "modes").write_text(
        "".join(f"{mode}\n" for mode in spec.get("modes", [])), encoding="utf-8"
    )
    (path / "enabled").write_text("enabled\n", encoding="utf-8")

    if spec.get("edid", True):
        block = build_edid(
            pnp_id=spec.get("pnp_id", "XXX"),
            model=spec.get("model", "Synthetic Display"),
            serial=spec.get("serial"),
            product_code=spec.get("product_code", 0x0001),
            serial_number=spec.get("serial_number", 0),
        )
        blob = corrupt(block, spec["corrupt"]) if spec.get("corrupt") else bytes(block)
        (path / "edid").write_bytes(blob)
    else:
        # A virtual output with no EDID at all; the parser must fall to the connector tier.
        (path / "edid").write_bytes(b"")

    return connector


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", type=Path, help="path to a display profile JSON file")
    parser.add_argument("destination", type=Path, help="directory to build the tree in")
    parser.add_argument("--card", default="card0", help="DRM card prefix for connector directories")
    parser.add_argument(
        "--connector-prefix",
        help="rewrite each connector's prefix, e.g. Virtual- to match a headless KWin",
    )
    parser.add_argument(
        "--print-connectors",
        action="store_true",
        help="print one connector name per line after building",
    )
    args = parser.parse_args()

    if re.fullmatch(r"card[0-9]+", args.card) is None:
        parser.error("--card must have the form cardN")
    if args.connector_prefix is not None and (
        not args.connector_prefix
        or re.fullmatch(r"[A-Za-z0-9_.:-]+", args.connector_prefix) is None
    ):
        parser.error("--connector-prefix must contain only connector-name characters")

    try:
        outputs = load_profile(args.profile)
    except ValueError as error:
        parser.error(str(error))

    created = False
    try:
        if args.destination.exists():
            parser.error(f"destination already exists: {args.destination}")
        args.destination.mkdir(parents=True)
        created = True
        connectors = [
            write_output(args.destination, args.card, spec, args.connector_prefix)
            for spec in outputs
        ]
        if len(set(connectors)) != len(connectors):
            raise ValueError("profile produces duplicate connector names")
    except (KeyError, OSError, TypeError, ValueError) as error:
        if created:
            shutil.rmtree(args.destination, ignore_errors=True)
        parser.error(str(error))
    if args.print_connectors:
        print("\n".join(connectors))
    return 0


if __name__ == "__main__":
    sys.exit(main())
