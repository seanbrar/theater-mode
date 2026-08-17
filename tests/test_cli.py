"""Unit tests for command-line interface and configuration parser."""

from __future__ import annotations

import unittest

from theater_mode.cli import build_effect_pipeline, parse_args
from theater_mode.effects.brightness import BrightnessEffect
from theater_mode.effects.composite import CompositeEffect
from theater_mode.effects.log import LogEffect
from theater_mode.effects.wallpaper import WallpaperEffect


class TestCLI(unittest.TestCase):
    def test_parse_defaults(self) -> None:
        args = parse_args([])
        self.assertEqual(args.effect, "log")
        self.assertEqual(args.dim_factor, 0.35)
        self.assertEqual(args.settle_seconds, 1.5)
        self.assertEqual(args.revert_delay, 3.0)
        self.assertEqual(args.stage_delay, 1.5)
        self.assertFalse(args.require_fullscreen)
        self.assertFalse(args.verbose)

    def test_parse_custom_flags(self) -> None:
        args = parse_args(
            [
                "--effect",
                "brightness,wallpaper",
                "--dim-factor",
                "0.20",
                "--revert-delay",
                "5.0",
                "--verbose",
            ]
        )
        self.assertEqual(args.effect, "brightness,wallpaper")
        self.assertEqual(args.dim_factor, 0.20)
        self.assertEqual(args.revert_delay, 5.0)
        self.assertTrue(args.verbose)

    def test_build_single_effect(self) -> None:
        effect = build_effect_pipeline("log", dim_factor=0.35, settle_seconds=1.5)
        self.assertIsInstance(effect, LogEffect)

        b_effect = build_effect_pipeline("brightness", dim_factor=0.25, settle_seconds=2.0)
        self.assertIsInstance(b_effect, BrightnessEffect)
        self.assertEqual(b_effect._dim_factor, 0.25)
        self.assertEqual(b_effect.transition_seconds, 2.0)

        w_effect = build_effect_pipeline("wallpaper", dim_factor=0.35, settle_seconds=1.5)
        self.assertIsInstance(w_effect, WallpaperEffect)

    def test_build_composite_effect(self) -> None:
        effect = build_effect_pipeline("brightness,wallpaper", dim_factor=0.20, settle_seconds=1.5)
        self.assertIsInstance(effect, CompositeEffect)
        self.assertEqual(effect.name, "brightness+wallpaper")

    def test_build_unknown_effect(self) -> None:
        with self.assertRaises(ValueError):
            build_effect_pipeline("unknown_effect", dim_factor=0.35, settle_seconds=1.5)


if __name__ == "__main__":
    unittest.main()
