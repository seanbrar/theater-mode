"""Tests for the nested harness synthetic DRM builder."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
MODULE_PATH = ROOT / "tools" / "nested" / "fake-drm.py"
sys.path.insert(0, str(ROOT / "src"))

from theater_mode.display.drm import output_identities  # noqa: E402
from theater_mode.display.edid import parse_edid  # noqa: E402


def load_fake_drm() -> ModuleType:
    """Load the hyphenated tool filename as a Python module."""
    spec = importlib.util.spec_from_file_location("fake_drm", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


fake_drm = load_fake_drm()


class FakeDrmTests(unittest.TestCase):
    """Exercise profile validation and EDID generation."""

    def test_repository_profiles_are_valid_and_have_unique_outputs(self) -> None:
        for path in sorted((MODULE_PATH.parent / "profiles").glob("*.json")):
            with self.subTest(profile=path.name):
                outputs = fake_drm.load_profile(path)
                connectors = [
                    fake_drm.rename_connector(spec["connector"], "WL-") for spec in outputs
                ]
                self.assertEqual(len(connectors), len(set(connectors)))

    def test_generated_edid_has_header_and_checksum(self) -> None:
        block = fake_drm.build_edid("DEL", "Test Panel", "ABC123", 1, 2)

        self.assertEqual(block[:8], fake_drm.EDID_HEADER)
        self.assertEqual(len(block), fake_drm.EDID_BLOCK_SIZE)
        self.assertEqual(sum(block) % 256, 0)

        identity = parse_edid("DP-1", bytes(block))
        self.assertEqual(identity.pnp_id, "DEL")
        self.assertEqual(identity.model, "Test Panel")
        self.assertEqual(identity.serial, "ABC123")

    def test_text_descriptor_formatting(self) -> None:
        short = fake_drm.text_descriptor(fake_drm.TAG_NAME, "Short")
        self.assertEqual(len(short), fake_drm.DESCRIPTOR_SIZE)
        self.assertTrue(short.startswith(b"\x00\x00\x00\xfc\x00Short\n"))

        long_name = fake_drm.text_descriptor(fake_drm.TAG_NAME, "VeryLongModelNameExceeding13")
        self.assertEqual(len(long_name), fake_drm.DESCRIPTOR_SIZE)
        self.assertEqual(long_name[5:], b"VeryLongModel")

    def test_corrupt_modes(self) -> None:
        block = fake_drm.build_edid("DEL", "Test Panel", None, 1, 2)

        checksum_corrupted = fake_drm.corrupt(bytearray(block), "checksum")
        self.assertNotEqual(sum(checksum_corrupted) % 256, 0)

        truncated = fake_drm.corrupt(bytearray(block), "truncated")
        self.assertEqual(len(truncated), 64)

        header_corrupted = fake_drm.corrupt(bytearray(block), "header")
        self.assertEqual(header_corrupted, bytes(fake_drm.EDID_BLOCK_SIZE))

        with self.assertRaisesRegex(ValueError, "unknown corruption mode"):
            fake_drm.corrupt(bytearray(block), "bogus")

    def test_rename_connector(self) -> None:
        self.assertEqual(fake_drm.rename_connector("DP-1", "WL-"), "WL-1")
        self.assertEqual(fake_drm.rename_connector("Virtual-2", "WL-"), "WL-2")
        self.assertEqual(fake_drm.rename_connector("DP-1", None), "DP-1")
        self.assertEqual(fake_drm.rename_connector("DP-1", ""), "DP-1")

    def test_write_output_rejects_paths_outside_the_drm_tree(self) -> None:
        spec = {"connector": "DP-1"}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(ValueError, "card must have the form"):
                fake_drm.write_output(root, "../card0", spec)
            with self.assertRaisesRegex(ValueError, "invalid generated connector"):
                fake_drm.write_output(root, "card0", spec, "../")

    def test_non_ascii_pnp_id_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "three letters"):
            fake_drm.pack_pnp_id("DÉL")

    def test_invalid_profile_reports_field(self) -> None:
        cases = [
            ({"outputs": [{"connector": "DP-1", "corrupt": "fire"}]}, r"outputs\[0\]\.corrupt"),
            ({"outputs": [{"connector": "DP-1", "pnp_id": "DELL"}]}, r"outputs\[0\]\.pnp_id"),
            ({"outputs": [{"connector": "DP-1", "status": "active"}]}, r"outputs\[0\]\.status"),
            ({"outputs": [{"connector": "DP-1", "modes": ["1080p"]}]}, r"outputs\[0\]\.modes"),
            ({"outputs": [{"connector": "DP-1", "model": None}]}, r"outputs\[0\]\.model"),
            ({"outputs": [{"connector": "DP-1", "typo": True}]}, "unknown field 'typo'"),
            (
                {"outputs": [{"connector": "DP-1", "product_code": 70000}]},
                r"outputs\[0\]\.product_code",
            ),
            ({"outputs": []}, "defines no outputs"),
            ({"outputs": [{"connector": "DP-1"}], "typo": True}, "unknown field 'typo'"),
            ("not a dict", "profile root must be an object"),
        ]
        for data, pattern in cases:
            with self.subTest(data=data), tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / "invalid.json"
                path.write_text(json.dumps(data), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, pattern):
                    fake_drm.load_profile(path)

    def test_write_output_and_drm_parsing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            profile_data = {
                "outputs": [
                    {
                        "connector": "DP-1",
                        "pnp_id": "GSM",
                        "model": "LG UltraGear",
                        "serial": "SN12345",
                        "product_code": 100,
                        "serial_number": 200,
                        "modes": ["2560x1440", "1920x1080"],
                    },
                    {
                        "connector": "DP-2",
                        "edid": False,
                        "modes": ["1920x1080"],
                    },
                ]
            }
            profile_path = root / "profile.json"
            profile_path.write_text(json.dumps(profile_data), encoding="utf-8")
            dest = root / "drm"

            outputs = fake_drm.load_profile(profile_path)
            connectors = [fake_drm.write_output(dest, "card0", spec, "WL-") for spec in outputs]
            self.assertEqual(connectors, ["WL-1", "WL-2"])

            with patch("theater_mode.display.drm.DRM_DIR", dest):
                identities = output_identities()
                self.assertIn("WL-1", identities)
                self.assertEqual(identities["WL-1"].model, "LG UltraGear")
                self.assertEqual(identities["WL-1"].serial, "SN12345")
                self.assertIn("WL-2", identities)
                self.assertIsNone(identities["WL-2"].model)


if __name__ == "__main__":
    unittest.main()
