"""Unit tests for command-line interface and configuration parser."""

from __future__ import annotations

import unittest

from theater_mode.cli import parse_args
from theater_mode.effects import EFFECTS
from theater_mode.effects.base import EffectOptions
from theater_mode.effects.dim import DimEffect
from theater_mode.effects.log import LogEffect


class TestCLI(unittest.TestCase):
    def test_parse_defaults(self) -> None:
        args = parse_args([])
        self.assertEqual(args.effect, "log")
        self.assertEqual(args.dim_factor, 0.85)
        self.assertEqual(args.dim_duration, 2.0)
        self.assertEqual(args.dim_curve, "sine")
        self.assertTrue(args.art)
        self.assertEqual(args.revert_delay, 3.0)
        self.assertEqual(args.stage_delay, 1.5)
        self.assertFalse(args.require_fullscreen)
        self.assertFalse(args.verbose)

    def test_parse_custom_flags(self) -> None:
        args = parse_args(
            [
                "--effect",
                "dim",
                "--dim-factor",
                "0.80",
                "--dim-duration",
                "3.5",
                "--dim-curve",
                "cubic",
                "--no-art",
                "--revert-delay",
                "5.0",
                "--verbose",
            ]
        )
        self.assertEqual(args.effect, "dim")
        self.assertEqual(args.dim_factor, 0.80)
        self.assertEqual(args.dim_duration, 3.5)
        self.assertEqual(args.dim_curve, "cubic")
        self.assertFalse(args.art)
        self.assertEqual(args.revert_delay, 5.0)
        self.assertTrue(args.verbose)

    def test_effects_are_built_from_options(self) -> None:
        options = EffectOptions(dim_factor=0.5, dim_duration=9.0, dim_curve="quad", art=False)

        effect = EFFECTS["dim"].create(options)
        self.assertIsInstance(effect, DimEffect)
        self.assertEqual(effect._dim_factor, 0.5)
        self.assertEqual(effect._duration, 9.0)
        self.assertEqual(effect._curve, "quad")
        self.assertFalse(effect._art)

        # Effects without settings ignore the options entirely.
        self.assertIsInstance(EFFECTS["log"].create(options), LogEffect)

    def test_dim_factor_bounds(self) -> None:
        # 0.0 means "no dimming" and is a legitimate value; 1.0 is fully black.
        self.assertEqual(parse_args(["--dim-factor", "0"]).dim_factor, 0.0)
        self.assertEqual(parse_args(["--dim-factor", "1"]).dim_factor, 1.0)
        for bad in ("-0.1", "1.1"):
            with self.assertRaises(SystemExit):
                parse_args(["--dim-factor", bad])

    def test_unknown_effect_is_rejected(self) -> None:
        with self.assertRaises(SystemExit):
            parse_args(["--effect", "unknown_effect"])


if __name__ == "__main__":
    unittest.main()
