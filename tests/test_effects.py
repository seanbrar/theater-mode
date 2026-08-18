"""Unit tests for the dry-run log effect."""

from __future__ import annotations

import unittest

from theater_mode.effects.log import LogEffect


class TestLogEffect(unittest.TestCase):
    def test_log_effect_touches_nothing(self) -> None:
        effect = LogEffect()
        self.assertEqual(effect.name, "log")
        with self.assertLogs("theater-moded", level="INFO"):
            effect.apply("DP-1", ["DP-2", "DP-3"], "1671210")
        with self.assertLogs("theater-moded", level="INFO"):
            effect.revert()


if __name__ == "__main__":
    unittest.main()
