"""Unit tests for data conversion, process inspection, and binary discovery utilities."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from theater_mode.utils import (
    find_helper_binary,
    parse_bool,
    parse_int,
    read_process_cmdline,
    read_process_environ,
)


class TestUtils(unittest.TestCase):
    def test_parse_bool(self) -> None:
        self.assertTrue(parse_bool("true"))
        self.assertTrue(parse_bool("True"))
        self.assertTrue(parse_bool("  TRUE  "))
        self.assertTrue(parse_bool(True))

        self.assertFalse(parse_bool("false"))
        self.assertFalse(parse_bool("False"))
        self.assertFalse(parse_bool(""))
        self.assertFalse(parse_bool("0"))
        self.assertFalse(parse_bool(False))

    def test_parse_int(self) -> None:
        self.assertEqual(parse_int("123"), 123)
        self.assertEqual(parse_int("-45"), -45)
        self.assertEqual(parse_int("  0  "), 0)
        self.assertEqual(parse_int(99), 99)
        self.assertEqual(parse_int("invalid"), 0)
        self.assertEqual(parse_int("invalid", default=10), 10)
        self.assertEqual(parse_int(None, default=5), 5)

    def test_read_process_cmdline(self) -> None:
        with tempfile.NamedTemporaryFile() as tf:
            tf.write(b"reaper\0SteamLaunch\0AppId=1222140\0--\0proton\0")
            tf.flush()
            with patch("theater_mode.utils.Path") as mock_path:
                mock_path.return_value.read_bytes.return_value = Path(tf.name).read_bytes()
                cmdline = read_process_cmdline(12345)
                self.assertEqual(cmdline, "reaper SteamLaunch AppId=1222140 -- proton")

    def test_read_process_environ(self) -> None:
        with tempfile.NamedTemporaryFile() as tf:
            tf.write(b"SteamGameId=1671210\0USER=sean\0DISPLAY=:0\0")
            tf.flush()
            with patch("theater_mode.utils.Path") as mock_path:
                mock_path.return_value.read_bytes.return_value = Path(tf.name).read_bytes()
                env = read_process_environ(12345)
                self.assertEqual(env.get("SteamGameId"), "1671210")
                self.assertEqual(env.get("USER"), "sean")
                self.assertEqual(env.get("DISPLAY"), ":0")

    def test_find_helper_binary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            fake_bin = Path(tmp_dir) / "helper-test"
            fake_bin.touch(mode=0o755)

            # 1. Environment variable override
            with patch.dict("os.environ", {"TEST_HELPER_BIN": str(fake_bin)}):
                self.assertEqual(
                    find_helper_binary("helper-test", "TEST_HELPER_BIN", "sub"), fake_bin
                )

            # 2. PATH resolution fallback
            with (
                patch.dict("os.environ", {}, clear=True),
                patch("pathlib.Path.is_file", return_value=False),
                patch("shutil.which", return_value=str(fake_bin)),
            ):
                self.assertEqual(
                    find_helper_binary("helper-test", "TEST_HELPER_BIN", "sub"), fake_bin
                )

            # 3. Not found
            with (
                patch.dict("os.environ", {}, clear=True),
                patch("pathlib.Path.is_file", return_value=False),
                patch("shutil.which", return_value=None),
            ):
                self.assertIsNone(find_helper_binary("helper-test", "TEST_HELPER_BIN", "sub"))


if __name__ == "__main__":
    unittest.main()
