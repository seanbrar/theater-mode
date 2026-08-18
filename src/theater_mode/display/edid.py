"""EDID parsing and stable per-output identity construction.

Outputs are addressed in configuration by identity rather than connector where possible,
because a connector name changes the moment a cable moves to a different port.
"""

from __future__ import annotations

import logging
import struct
from dataclasses import dataclass
from functools import cache
from pathlib import Path

log = logging.getLogger("theater-moded")

EDID_HEADER = b"\x00\xff\xff\xff\xff\xff\xff\x00"
EDID_BLOCK_SIZE = 128

# Display descriptors live at bytes 54..125 in four 18-byte slots.
_DESCRIPTOR_START = 54
_DESCRIPTOR_SIZE = 18
_DESCRIPTOR_COUNT = 4
_TAG_SERIAL = 0xFF
_TAG_NAME = 0xFC

# Maps the 3-letter EDID vendor code to a full vendor name (the "GSM" -> "LG Electronics"
# table that KWin and kscreen also use). Optional; absent on minimal installs.
PNP_IDS_PATH = Path("/usr/share/hwdata/pnp.ids")


@cache
def _pnp_vendor_names() -> dict[str, str]:
    """Load the hwdata PnP vendor table, or an empty mapping when it is unavailable."""
    try:
        text = PNP_IDS_PATH.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}

    names: dict[str, str] = {}
    for line in text.splitlines():
        code, _, name = line.partition("\t")
        if len(code) == 3 and name.strip():
            names[code.upper()] = name.strip()
    return names


def _decode_pnp_id(raw: bytes) -> str | None:
    """Decode the two-byte manufacturer field into its 3-letter PnP code."""
    (packed,) = struct.unpack(">H", raw)
    letters = [(packed >> shift) & 0x1F for shift in (10, 5, 0)]
    if not all(1 <= value <= 26 for value in letters):
        return None
    return "".join(chr(ord("A") + value - 1) for value in letters)


def _descriptor_text(block: bytes, tag: int) -> str | None:
    """Return the text of the first display descriptor carrying the given tag."""
    for index in range(_DESCRIPTOR_COUNT):
        start = _DESCRIPTOR_START + index * _DESCRIPTOR_SIZE
        descriptor = block[start : start + _DESCRIPTOR_SIZE]
        # A display descriptor is flagged by a zero pixel clock in its first two bytes.
        if descriptor[0:2] != b"\x00\x00" or descriptor[3] != tag:
            continue
        text = descriptor[5:].split(b"\n")[0].decode("ascii", errors="replace").strip()
        if text:
            return text
    return None


@dataclass(frozen=True, slots=True)
class OutputIdentity:
    """Identifiers for one connected output, from most to least stable."""

    connector: str
    vendor: str | None = None
    pnp_id: str | None = None
    model: str | None = None
    serial: str | None = None

    @property
    def match_keys(self) -> tuple[str, ...]:
        """Configuration keys to try in priority order, most specific first.

        Both the resolved vendor name and the raw PnP code are offered at each tier, so a
        config written as 'LG Electronics:27GL850' keeps working on a host without the
        hwdata PnP table (where only 'GSM:27GL850' can be derived), and vice versa.
        """
        makes = [name for name in (self.vendor, self.pnp_id) if name]
        if not self.model:
            return ()

        keys = []
        if self.serial:
            keys += [f"{make}:{self.model}:{self.serial}" for make in makes]
        keys += [f"{make}:{self.model}" for make in makes]
        return tuple(keys)


def parse_edid(connector: str, blob: bytes) -> OutputIdentity:
    """Parse an EDID blob into an OutputIdentity, degrading to connector-only on any fault.

    A missing, truncated, or corrupt blob is normal (sleeping displays, virtual outputs,
    KVM switches), so this never raises: the caller still gets a usable connector match.
    """
    identity = OutputIdentity(connector=connector)
    if len(blob) < EDID_BLOCK_SIZE or not blob.startswith(EDID_HEADER):
        return identity

    block = blob[:EDID_BLOCK_SIZE]
    if sum(block) % 256 != 0:
        log.debug("EDID checksum mismatch on %s; using connector name only", connector)
        return identity

    pnp_id = _decode_pnp_id(block[8:10])
    product_code, serial_number = struct.unpack("<HI", block[10:16])

    # Prefer the descriptor strings a vendor writes for humans over the numeric fields.
    model = _descriptor_text(block, _TAG_NAME) or f"{product_code:04X}"
    serial = _descriptor_text(block, _TAG_SERIAL) or (
        f"0x{serial_number:04x}" if serial_number else None
    )

    return OutputIdentity(
        connector=connector,
        vendor=_pnp_vendor_names().get(pnp_id or ""),
        pnp_id=pnp_id,
        model=model,
        serial=serial,
    )
