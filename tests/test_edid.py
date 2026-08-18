"""Unit tests for EDID parsing and per-output identity construction."""

from __future__ import annotations

import struct
import unittest
from unittest.mock import patch

from theater_mode.display.edid import EDID_HEADER, OutputIdentity, parse_edid


def build_edid(
    pnp_id: str = "DEL",
    product_code: int = 0x4131,
    serial_number: int = 0x00010203,
    monitor_name: str | None = "DELL S2721QS",
    serial_text: str | None = "4QCPZY3",
) -> bytes:
    """Assemble a minimal but structurally valid 128-byte EDID base block."""
    packed = 0
    for letter in pnp_id:
        packed = (packed << 5) | (ord(letter) - ord("A") + 1)

    block = bytearray(128)
    block[0:8] = EDID_HEADER
    block[8:10] = struct.pack(">H", packed)
    block[10:16] = struct.pack("<HI", product_code, serial_number)
    block[18:20] = b"\x01\x04"  # EDID 1.4

    descriptors = [(0xFF, serial_text), (0xFC, monitor_name)]
    for index, (tag, text) in enumerate(descriptors):
        if text is None:
            continue
        start = 54 + index * 18
        block[start : start + 5] = bytes([0x00, 0x00, 0x00, tag, 0x00])
        payload = text.encode("ascii")[:13]
        if len(payload) < 13:
            payload += b"\n" + b" " * (12 - len(payload))
        block[start + 5 : start + 18] = payload

    block[127] = (-sum(block[:127])) % 256
    return bytes(block)


class TestParseEdid(unittest.TestCase):
    def setUp(self) -> None:
        # Keep the hwdata lookup deterministic regardless of the host's packages.
        patcher = patch(
            "theater_mode.display.edid._pnp_vendor_names",
            return_value={"DEL": "Dell Inc.", "GSM": "LG Electronics"},
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_parses_vendor_model_and_serial(self) -> None:
        identity = parse_edid("DP-2", build_edid())

        self.assertEqual(identity.connector, "DP-2")
        self.assertEqual(identity.vendor, "Dell Inc.")
        self.assertEqual(identity.pnp_id, "DEL")
        self.assertEqual(identity.model, "DELL S2721QS")
        self.assertEqual(identity.serial, "4QCPZY3")

    def test_match_keys_are_ordered_most_specific_first(self) -> None:
        identity = parse_edid("DP-2", build_edid())

        self.assertEqual(
            identity.match_keys,
            (
                "Dell Inc.:DELL S2721QS:4QCPZY3",
                "DEL:DELL S2721QS:4QCPZY3",
                "Dell Inc.:DELL S2721QS",
                "DEL:DELL S2721QS",
            ),
        )

    def test_raw_pnp_id_is_offered_when_hwdata_is_missing(self) -> None:
        with patch("theater_mode.display.edid._pnp_vendor_names", return_value={}):
            identity = parse_edid("DP-2", build_edid())

        self.assertIsNone(identity.vendor)
        self.assertEqual(identity.match_keys, ("DEL:DELL S2721QS:4QCPZY3", "DEL:DELL S2721QS"))

    def test_numeric_serial_is_used_without_a_serial_descriptor(self) -> None:
        identity = parse_edid("DP-2", build_edid(serial_text=None, serial_number=1))
        self.assertEqual(identity.serial, "0x0001")

    def test_product_code_is_used_without_a_name_descriptor(self) -> None:
        identity = parse_edid("DP-2", build_edid(monitor_name=None, product_code=0x4131))
        self.assertEqual(identity.model, "4131")

    def test_output_with_no_serial_still_matches_on_make_model(self) -> None:
        identity = parse_edid("DP-2", build_edid(serial_text=None, serial_number=0))
        self.assertIsNone(identity.serial)
        self.assertEqual(identity.match_keys, ("Dell Inc.:DELL S2721QS", "DEL:DELL S2721QS"))


class TestMalformedEdid(unittest.TestCase):
    def assert_connector_only(self, blob: bytes) -> None:
        identity = parse_edid("HDMI-A-1", blob)
        self.assertEqual(identity, OutputIdentity(connector="HDMI-A-1"))
        self.assertEqual(identity.match_keys, ())

    def test_empty_blob(self) -> None:
        # Normal for sleeping displays, virtual outputs, and KVM switches.
        self.assert_connector_only(b"")

    def test_truncated_blob(self) -> None:
        self.assert_connector_only(build_edid()[:64])

    def test_missing_header(self) -> None:
        self.assert_connector_only(bytes(128))

    def test_bad_checksum(self) -> None:
        corrupt = bytearray(build_edid())
        corrupt[127] ^= 0xFF
        self.assert_connector_only(bytes(corrupt))

    def test_unparseable_manufacturer_falls_back_to_connector(self) -> None:
        block = bytearray(build_edid(monitor_name=None, serial_text=None, serial_number=0))
        block[8:10] = b"\x00\x00"  # letter values outside 1..26
        block[127] = (-sum(block[:127])) % 256

        identity = parse_edid("DP-1", bytes(block))
        self.assertIsNone(identity.pnp_id)
        self.assertEqual(identity.match_keys, ())


if __name__ == "__main__":
    unittest.main()
