"""Unit tests for the Wayland DimEffect and its helper IPC."""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from tests.test_edid import build_edid
from theater_mode.config import (
    EffectConfig,
    OutputOverrideConfig,
    ResolvedConfig,
    TransitionConfig,
)
from theater_mode.display.edid import parse_edid
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

        identities = patch("theater_mode.effects.dim.output_identities", return_value={})
        self.output_identities = identities.start()
        self.addCleanup(identities.stop)

        popen = patch("theater_mode.effects.dim.subprocess.Popen", return_value=self.process)
        self.popen = popen.start()
        self.addCleanup(popen.stop)


class TestDimCommands(DimEffectTestCase):
    def test_apply_stages_artwork_before_dimming(self) -> None:
        effect = DimEffect(dimming=0.85, duration=2.0, curve="sine")
        self.assertEqual(effect.name, "dim")

        effect.apply("DP-1", ["DP-3", "DP-2"], "1245620")

        self.assertEqual(
            written(self.process),
            [
                "LAYER DP-2 overlay",
                "ART DP-2 1920 1080 /cache/art.argb",
                "LAYER DP-3 overlay",
                "ART DP-3 1920 1080 /cache/art.argb",
                "DIM DP-2,DP-3 0.850 2.00 sine",
            ],
        )

    def test_artwork_buffers_are_capped_at_1080p(self) -> None:
        effect = DimEffect(dimming=0.85)
        effect.apply("DP-1", ["DP-2", "DP-3"], "1245620")

        self.assertEqual(
            [call.args for call in self.build_artwork.call_args_list],
            [("1245620", 1920, 1080, 0.85)],
        )

    def test_dimming_is_part_of_the_artwork_request(self) -> None:
        DimEffect(dimming=0.4).apply("DP-1", ["DP-2"], "1245620")
        self.assertEqual(self.build_artwork.call_args.args, ("1245620", 1920, 1080, 0.4))

    def test_revert_fades_out(self) -> None:
        effect = DimEffect(duration=2.0, curve="sine")
        effect.apply("DP-1", ["DP-2"], "1245620")

        self.process.stdin.write.reset_mock()
        effect.revert()
        self.assertEqual(written(self.process), ["FADE_OUT 2.000 sine"])

    def test_revert_immediate_also_quits_the_helper(self) -> None:
        effect = DimEffect(dimming=0.80, duration=1.5)
        effect.apply("DP-1", ["DP-2"], "1245620")

        self.process.stdin.write.reset_mock()
        effect.revert(immediate=True)
        self.assertEqual(written(self.process), ["FADE_OUT 0.001 sine", "QUIT"])

    def test_apply_with_no_secondary_outputs_reverts(self) -> None:
        DimEffect().apply("DP-1", [], "1245620")
        self.popen.assert_not_called()


class TestPerOutputSettings(DimEffectTestCase):
    def config(self, **overrides: OutputOverrideConfig) -> ResolvedConfig:
        return ResolvedConfig(
            effect=EffectConfig(dimming=0.85, artwork=False),
            transition=TransitionConfig(duration=2.0, curve="sine"),
            outputs=dict(overrides),
        )

    def test_every_output_stays_in_the_dimmed_set(self) -> None:
        """A per-output override must not fade the other secondary displays back out."""
        effect = DimEffect(
            artwork=False,
            resolved_config=self.config(**{"DP-3": OutputOverrideConfig(dimming=0.4)}),
        )
        effect.apply("DP-1", ["DP-2", "DP-3"], "1245620")

        commands = written(self.process)
        self.assertIn("DIM DP-2,DP-3 0.850 2.00 sine", commands)
        self.assertIn("DIM_OUTPUT DP-3 0.400 2.00 sine", commands)
        self.assertNotIn("DIM DP-3 0.400 2.00 sine", commands)

    def test_zero_dimming_leaves_one_output_untouched(self) -> None:
        effect = DimEffect(
            artwork=False,
            resolved_config=self.config(**{"DP-3": OutputOverrideConfig(dimming=0.0)}),
        )
        effect.apply("DP-1", ["DP-2", "DP-3"], "1245620")

        commands = written(self.process)
        self.assertEqual(commands, ["LAYER DP-2 overlay", "ART DP-2", "DIM DP-2 0.850 2.00 sine"])
        self.assertFalse([c for c in commands if "DP-3" in c])
        self.assertEqual(effect.affected_outputs, ("DP-2",))

    def test_zero_dimming_everywhere_reverts_instead_of_dimming(self) -> None:
        effect = DimEffect(
            artwork=False,
            resolved_config=ResolvedConfig(
                effect=EffectConfig(dimming=0.0, artwork=False),
                transition=TransitionConfig(duration=2.0, curve="sine"),
            ),
        )
        effect.apply("DP-1", ["DP-2", "DP-3"], "1245620")

        self.assertFalse([c for c in written(self.process) if c.startswith(("DIM", "ART"))])
        self.assertEqual(effect.affected_outputs, ())

    def test_outputs_matching_the_globals_are_not_retuned(self) -> None:
        effect = DimEffect(artwork=False, resolved_config=self.config())
        effect.apply("DP-1", ["DP-2", "DP-3"], "1245620")

        self.assertEqual(
            written(self.process),
            [
                "LAYER DP-2 overlay",
                "ART DP-2",
                "LAYER DP-3 overlay",
                "ART DP-3",
                "DIM DP-2,DP-3 0.850 2.00 sine",
            ],
        )

    def test_per_output_duration_and_curve_are_applied(self) -> None:
        effect = DimEffect(
            artwork=False,
            resolved_config=self.config(
                **{"DP-2": OutputOverrideConfig(duration=0.5, curve="linear")}
            ),
        )
        effect.apply("DP-1", ["DP-2"], "1245620")

        self.assertIn("DIM_OUTPUT DP-2 0.850 0.50 linear", written(self.process))

    def test_per_output_placement_is_applied(self) -> None:
        effect = DimEffect(
            artwork=False,
            resolved_config=self.config(
                **{"DP-2": OutputOverrideConfig(placement="behind_windows")}
            ),
        )
        effect.apply("DP-1", ["DP-2", "DP-3"], "1245620")

        commands = written(self.process)
        self.assertIn("LAYER DP-2 bottom", commands)
        self.assertIn("LAYER DP-3 overlay", commands)

    def test_matched_rule_is_logged_as_a_copy_pasteable_header(self) -> None:
        self.output_identities.return_value = {
            "DP-2": parse_edid("DP-2", build_edid(serial_text="AAA1111")),
        }
        effect = DimEffect(
            artwork=False,
            resolved_config=self.config(
                **{"DEL:DELL S2721QS:AAA1111": OutputOverrideConfig(dimming=0.4)}
            ),
        )

        with self.assertLogs("theater-moded", level="INFO") as logs:
            effect.apply("DP-1", ["DP-2"], "1245620")

        matched = [line for line in logs.output if "matched" in line]
        self.assertEqual(len(matched), 1)
        self.assertIn('[outputs."DEL:DELL S2721QS:AAA1111"]', matched[0])
        self.assertIn("dimming=0.4", matched[0])

    def test_outputs_without_a_rule_are_not_logged(self) -> None:
        effect = DimEffect(artwork=False, resolved_config=self.config())

        with self.assertLogs("theater-moded", level="INFO") as logs:
            effect.apply("DP-1", ["DP-2", "DP-3"], "1245620")

        self.assertFalse([line for line in logs.output if "matched" in line])

    def test_edid_identity_outranks_the_connector_name(self) -> None:
        """Two identical panels must be addressable by serial, not just by port."""
        self.output_identities.return_value = {
            "DP-2": parse_edid("DP-2", build_edid(serial_text="AAA1111")),
            "DP-3": parse_edid("DP-3", build_edid(serial_text="BBB2222")),
        }
        effect = DimEffect(
            artwork=False,
            resolved_config=self.config(
                **{
                    "DEL:DELL S2721QS:BBB2222": OutputOverrideConfig(dimming=0.4),
                    "DP-2": OutputOverrideConfig(dimming=0.7),
                }
            ),
        )
        effect.apply("DP-1", ["DP-2", "DP-3"], "1245620")

        commands = written(self.process)
        self.assertIn("DIM_OUTPUT DP-2 0.700 2.00 sine", commands)
        self.assertIn("DIM_OUTPUT DP-3 0.400 2.00 sine", commands)

    def test_per_output_dimming_reaches_the_artwork(self) -> None:
        effect = DimEffect(
            resolved_config=ResolvedConfig(
                effect=EffectConfig(dimming=0.85, artwork=True),
                outputs={"DP-3": OutputOverrideConfig(dimming=0.4)},
            )
        )
        effect.apply("DP-1", ["DP-2", "DP-3"], "1245620")

        self.assertEqual(
            [call.args for call in self.build_artwork.call_args_list],
            [("1245620", 1920, 1080, 0.85), ("1245620", 1920, 1080, 0.4)],
        )


class TestArtworkHandling(DimEffectTestCase):
    def test_artwork_disabled_clears_every_output(self) -> None:
        effect = DimEffect(artwork=False)
        effect.apply("DP-1", ["DP-2"], "1245620")

        self.assertEqual(
            written(self.process), ["LAYER DP-2 overlay", "ART DP-2", "DIM DP-2 0.850 2.00 sine"]
        )
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

        self.assertEqual(
            written(self.process), ["LAYER DP-2 overlay", "ART DP-2", "DIM DP-2 0.850 2.00 sine"]
        )

    def test_a_missing_appid_clears_artwork(self) -> None:
        effect = DimEffect()
        effect.apply("DP-1", ["DP-2"], "")

        self.assertEqual(
            written(self.process), ["LAYER DP-2 overlay", "ART DP-2", "DIM DP-2 0.850 2.00 sine"]
        )
        self.build_artwork.assert_not_called()

    def test_art_command_formats_path_correctly(self) -> None:
        command = DimEffect.art_command("DP-2", Path("/home/user/.cache/art.argb"), (3840, 2160))
        self.assertEqual(command, "ART DP-2 3840 2160 /home/user/.cache/art.argb")
        self.assertTrue(command.endswith("/home/user/.cache/art.argb"))

    def test_layer_command_translates_placement_to_protocol(self) -> None:
        self.assertEqual(DimEffect.layer_command("DP-2", "behind_windows"), "LAYER DP-2 bottom")
        self.assertEqual(DimEffect.layer_command("DP-2", "over_windows"), "LAYER DP-2 overlay")


class TestHelperLifecycle(DimEffectTestCase):
    def test_never_reads_from_the_helper(self) -> None:
        effect = DimEffect()
        effect.apply("DP-1", ["DP-2"], "1245620")
        effect.revert()

        self.process.stdout.readline.assert_not_called()
        self.assertIs(self.popen.call_args.kwargs["stdout"], subprocess.DEVNULL)
        self.assertNotIn("stderr", self.popen.call_args.kwargs)

    def test_apply_reports_success_and_failure(self) -> None:
        effect = DimEffect(artwork=False)
        self.assertTrue(effect.apply("DP-1", ["DP-2"], "1245620"))

        dead = make_process()
        dead.stdin.write.side_effect = BrokenPipeError("gone")
        self.popen.return_value = dead
        broken_effect = DimEffect(artwork=False)
        self.assertFalse(broken_effect.apply("DP-1", ["DP-2"], "1245620"))

    def test_is_running_reports_process_state(self) -> None:
        effect = DimEffect(artwork=False)
        self.assertFalse(effect.is_running)

        effect.apply("DP-1", ["DP-2"], "1245620")
        self.assertTrue(effect.is_running)

        self.process.poll.return_value = 1
        self.assertFalse(effect.is_running)

    def test_broken_pipe_logs_error_and_cleans_up(self) -> None:
        dead = make_process()
        dead.stdin.write.side_effect = BrokenPipeError("gone")
        self.popen.return_value = dead

        effect = DimEffect(artwork=False)
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
    @patch("shutil.which")
    def test_find_dimmer_binary(self, mock_which) -> None:
        mock_which.return_value = "/usr/bin/theater-dimmer"
        self.assertIsNotNone(find_dimmer_binary())


if __name__ == "__main__":
    unittest.main()
