"""Unit tests for the Wayland DimEffect and its helper IPC."""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from theater_mode.effects.dim import DimEffect, find_dimmer_binary


def make_process(alive: bool = True) -> MagicMock:
    """Mock subprocess representing the theater-dimmer helper."""
    process = MagicMock()
    process.poll.return_value = None if alive else 0
    process.pid = 4242
    return process


def written(process: MagicMock) -> list[str]:
    """Return all commands written to the helper stdin."""
    return [call.args[0].rstrip("\n") for call in process.stdin.write.call_args_list]


class DimEffectTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.process = make_process()

        for target, value in (
            ("theater_mode.effects.dim.find_dimmer_binary", "/usr/bin/theater-dimmer"),
            ("theater_mode.effects.dim.output_modes", {"DP-2": (3840, 2160), "DP-3": (1920, 1080)}),
            ("theater_mode.effects.dim.build_artwork", Path("/cache/art.argb")),
        ):
            patcher = patch(target, return_value=value)
            setattr(self, target.rsplit(".", 1)[1], patcher.start())
            self.addCleanup(patcher.stop)

        popen = patch("theater_mode.effects.dim.subprocess.Popen", return_value=self.process)
        self.popen = popen.start()
        self.addCleanup(popen.stop)


class TestDimCommands(DimEffectTestCase):
    def test_apply_stages_artwork_before_dimming(self) -> None:
        effect = DimEffect(dim_factor=0.85, duration=2.0, curve="sine")
        self.assertEqual(effect.name, "dim")

        effect.apply("DP-1", ["DP-3", "DP-2"], "1245620")

        self.assertEqual(
            written(self.process),
            [
                "ART DP-2 3840 2160 /cache/art.argb",
                "ART DP-3 1920 1080 /cache/art.argb",
                "DIM DP-2,DP-3 0.850 2.00 sine",
            ],
        )

    def test_artwork_is_built_at_each_output_native_size(self) -> None:
        effect = DimEffect(dim_factor=0.85)
        effect.apply("DP-1", ["DP-2", "DP-3"], "1245620")

        self.assertEqual(
            [call.args for call in self.build_artwork.call_args_list],
            [("1245620", 3840, 2160, 0.85), ("1245620", 1920, 1080, 0.85)],
        )

    def test_dim_factor_is_part_of_the_artwork_request(self) -> None:
        DimEffect(dim_factor=0.4).apply("DP-1", ["DP-2"], "1245620")
        self.assertEqual(self.build_artwork.call_args.args, ("1245620", 3840, 2160, 0.4))

    def test_revert_fades_out(self) -> None:
        effect = DimEffect(duration=2.0, curve="sine")
        effect.apply("DP-1", ["DP-2"], "1245620")

        self.process.stdin.write.reset_mock()
        effect.revert()
        self.assertEqual(written(self.process), ["FADE_OUT 2.000 sine"])

    def test_revert_immediate_also_quits_the_helper(self) -> None:
        effect = DimEffect(dim_factor=0.80, duration=1.5)
        effect.apply("DP-1", ["DP-2"], "1245620")

        self.process.stdin.write.reset_mock()
        effect.revert(immediate=True)
        self.assertEqual(written(self.process), ["FADE_OUT 0.001 sine", "QUIT"])

    def test_apply_with_no_secondary_outputs_reverts(self) -> None:
        DimEffect().apply("DP-1", [], "1245620")
        self.popen.assert_not_called()


class TestArtworkHandling(DimEffectTestCase):
    def test_artwork_disabled_clears_every_output(self) -> None:
        effect = DimEffect(art=False)
        effect.apply("DP-1", ["DP-2"], "1245620")

        self.assertEqual(written(self.process), ["ART DP-2", "DIM DP-2 0.850 2.00 sine"])
        self.build_artwork.assert_not_called()
        self.output_modes.assert_not_called()

    def test_an_output_with_no_known_mode_is_cleared(self) -> None:
        self.output_modes.return_value = {"DP-2": (3840, 2160)}
        effect = DimEffect()
        effect.apply("DP-1", ["DP-2", "DP-9"], "1245620")

        self.assertIn("ART DP-9", written(self.process))

    def test_a_game_without_hero_art_still_dims(self) -> None:
        self.build_artwork.return_value = None
        effect = DimEffect()
        effect.apply("DP-1", ["DP-2"], "1245620")

        self.assertEqual(written(self.process), ["ART DP-2", "DIM DP-2 0.850 2.00 sine"])

    def test_a_missing_appid_clears_artwork(self) -> None:
        effect = DimEffect()
        effect.apply("DP-1", ["DP-2"], "")

        self.assertEqual(written(self.process), ["ART DP-2", "DIM DP-2 0.850 2.00 sine"])
        self.build_artwork.assert_not_called()

    def test_art_command_formats_path_correctly(self) -> None:
        command = DimEffect.art_command("DP-2", Path("/home/user/.cache/art.argb"), (3840, 2160))
        self.assertEqual(command, "ART DP-2 3840 2160 /home/user/.cache/art.argb")
        self.assertTrue(command.endswith("/home/user/.cache/art.argb"))


class TestHelperLifecycle(DimEffectTestCase):
    def test_never_reads_from_the_helper(self) -> None:
        effect = DimEffect()
        effect.apply("DP-1", ["DP-2"], "1245620")
        effect.revert()

        self.process.stdout.readline.assert_not_called()
        self.assertIs(self.popen.call_args.kwargs["stdout"], subprocess.DEVNULL)
        self.assertNotIn("stderr", self.popen.call_args.kwargs)

    def test_broken_pipe_logs_error_and_cleans_up(self) -> None:
        dead = make_process()
        dead.stdin.write.side_effect = BrokenPipeError("gone")
        self.popen.return_value = dead

        effect = DimEffect(art=False)
        with self.assertLogs("theater-moded", level="ERROR"):
            effect.apply("DP-1", ["DP-2"], "1245620")

        self.assertFalse(effect._dimmed)
        self.assertIsNone(effect._process)

    def test_revert_does_not_start_a_helper(self) -> None:
        effect = DimEffect()
        effect.revert()
        effect.revert(immediate=True)

        self.popen.assert_not_called()

    def test_dead_helper_is_not_resurrected_to_revert(self) -> None:
        effect = DimEffect()
        effect.apply("DP-1", ["DP-2"], "1245620")
        self.assertEqual(self.popen.call_count, 1)

        self.process.poll.return_value = 0
        effect.revert()

        self.assertEqual(self.popen.call_count, 1)


class TestBinaryDiscovery(unittest.TestCase):
    @patch("theater_mode.effects.dim.shutil.which")
    def test_find_dimmer_binary(self, mock_which) -> None:
        mock_which.return_value = "/usr/bin/theater-dimmer"
        self.assertIsNotNone(find_dimmer_binary())


if __name__ == "__main__":
    unittest.main()
