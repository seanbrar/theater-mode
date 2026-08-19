"""Unit tests for removing keys from the user configuration file."""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from theater_mode.client import main as client_main
from theater_mode.config import DaemonConfig, DevConfig, ResolvedConfig
from theater_mode.config.writer import remove_toml_keys, unset_user_config
from theater_mode.daemon import Daemon


class TestRemoveTomlKeys(unittest.TestCase):
    """The writer is format-preserving, so removal must disturb nothing but the key line."""

    def test_removes_only_the_targeted_key(self) -> None:
        original = '[effect]\ndim_factor = 0.80\nplacement = "behind_windows"\n'
        text, removed = remove_toml_keys(original, {"effect.placement"})
        self.assertEqual(removed, {"effect.placement"})
        self.assertEqual(text, "[effect]\ndim_factor = 0.80\n")

    def test_preserves_comments_blank_lines_and_headers(self) -> None:
        original = (
            "# top of file\n"
            "\n"
            "[effect]\n"
            "# how dark the other screens go\n"
            "dim_factor = 0.80  # trailing\n"
            'placement = "behind_windows"\n'
            "\n"
            '#[outputs."Dell Inc.:DELL S2721QS:4QCPZY3"]\n'
            "#dim_factor = 0.3\n"
        )
        text, removed = remove_toml_keys(original, {"effect.placement"})
        self.assertEqual(removed, {"effect.placement"})
        self.assertIn("# top of file", text)
        self.assertIn("# how dark the other screens go", text)
        self.assertIn("dim_factor = 0.80  # trailing", text)
        self.assertIn('#[outputs."Dell Inc.:DELL S2721QS:4QCPZY3"]', text)
        self.assertNotIn("placement", text)

    def test_only_removes_from_the_matching_table(self) -> None:
        original = "[effect]\ndim_factor = 0.5\n\n[outputs.DP-1]\ndim_factor = 0.9\n"
        text, removed = remove_toml_keys(original, {"outputs.DP-1.dim_factor"})
        self.assertEqual(removed, {"outputs.DP-1.dim_factor"})
        self.assertIn("[effect]\ndim_factor = 0.5", text)
        self.assertNotIn("0.9", text)

    def test_quoted_output_table_with_dots_in_the_id(self) -> None:
        original = '[outputs."Dell Inc.:DELL S2721QS:4QCPZY3"]\ndim_factor = 0.3\n'
        text, removed = remove_toml_keys(
            original, {"outputs.Dell Inc.:DELL S2721QS:4QCPZY3.dim_factor"}
        )
        self.assertEqual(removed, {"outputs.Dell Inc.:DELL S2721QS:4QCPZY3.dim_factor"})
        self.assertNotIn("dim_factor", text)
        self.assertIn("[outputs.", text)

    def test_absent_key_reports_nothing_removed_and_changes_nothing(self) -> None:
        original = "[effect]\ndim_factor = 0.5\n"
        text, removed = remove_toml_keys(original, {"effect.placement"})
        self.assertEqual(removed, set())
        self.assertEqual(text, original)

    def test_emptied_table_keeps_its_header(self) -> None:
        original = "# my monitor\n[outputs.DP-1]\ndim_factor = 0.3\n"
        text, removed = remove_toml_keys(original, {"outputs.DP-1.dim_factor"})
        self.assertEqual(removed, {"outputs.DP-1.dim_factor"})
        self.assertIn("# my monitor", text)
        self.assertIn("[outputs.DP-1]", text)

    def test_multiple_keys_across_tables(self) -> None:
        original = '[effect]\ndim_factor = 0.5\nmode = "dim"\n\n[daemon]\nrevert_delay = 2.0\n'
        text, removed = remove_toml_keys(original, {"effect.mode", "daemon.revert_delay"})
        self.assertEqual(removed, {"effect.mode", "daemon.revert_delay"})
        self.assertIn("dim_factor = 0.5", text)
        self.assertNotIn("revert_delay", text)

    def test_malformed_key_path_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            remove_toml_keys("[effect]\n", {"nodots"})


class TestUnsetUserConfig(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "config.toml"

    def test_missing_file_is_not_an_error(self) -> None:
        ok, msg, removed = unset_user_config({"effect.dim_factor"}, self.path)
        self.assertTrue(ok)
        self.assertEqual(removed, set())
        self.assertIn("No user configuration file", msg)

    def test_removes_and_rewrites_atomically(self) -> None:
        self.path.write_text('[effect]\ndim_factor = 0.8\nplacement = "behind_windows"\n')
        ok, _msg, removed = unset_user_config({"effect.dim_factor"}, self.path)
        self.assertTrue(ok)
        self.assertEqual(removed, {"effect.dim_factor"})
        self.assertEqual(self.path.read_text(), '[effect]\nplacement = "behind_windows"\n')
        # No temporary files left behind.
        self.assertEqual([p.name for p in self.path.parent.iterdir()], ["config.toml"])

    def test_no_matching_key_leaves_the_file_untouched(self) -> None:
        original = "[effect]\ndim_factor = 0.8\n"
        self.path.write_text(original)
        ok, msg, removed = unset_user_config({"effect.placement"}, self.path)
        self.assertTrue(ok)
        self.assertEqual(removed, set())
        self.assertIn("No matching keys", msg)
        self.assertEqual(self.path.read_text(), original)


class TestDaemonUnset(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "user.toml"
        self.daemon = Daemon(
            effect=MagicMock(),
            config=ResolvedConfig(daemon=DaemonConfig()),
            dev_config=DevConfig(user_config_override=self.path),
        )

    def test_unset_removes_a_committed_key_and_reloads(self) -> None:
        self.daemon.commit('{"effect.dim_factor": 0.4}')
        self.assertEqual(self.daemon.config.effect.dim_factor, 0.4)

        result = self.daemon.unset('["effect.dim_factor"]')
        self.assertIn("unset 1 keys", result)
        self.assertNotIn("dim_factor", self.path.read_text())
        # The resolved value must fall back rather than keep the removed override.
        self.assertNotEqual(self.daemon.config.effect.dim_factor, 0.4)

    def test_unset_quoted_output_key(self) -> None:
        self.daemon.commit(json.dumps({'outputs."Dell Inc.:DELL S2721QS:4QCPZY3".dim_factor': 0.3}))
        result = self.daemon.unset(
            json.dumps(['outputs."Dell Inc.:DELL S2721QS:4QCPZY3".dim_factor'])
        )
        self.assertEqual(result, "unset 1 keys")
        self.assertNotIn("dim_factor", self.path.read_text())

    def test_unknown_key_is_rejected_rather_than_silently_accepted(self) -> None:
        result = self.daemon.unset('["effect.nonsense"]')
        self.assertTrue(result.startswith("error: nothing to unset"))
        self.assertIn("rejected:", result)

    def test_known_key_that_is_not_set_reports_already_unset(self) -> None:
        result = self.daemon.unset('["effect.dim_factor"]')
        self.assertIn("unset 0 keys", result)
        self.assertIn("already unset: effect.dim_factor", result)
        self.assertFalse(result.startswith("error"))

    def test_mixed_known_and_unknown_keys(self) -> None:
        self.daemon.commit('{"effect.dim_factor": 0.4}')
        result = self.daemon.unset('["effect.dim_factor", "effect.bogus"]')
        self.assertIn("unset 1 keys", result)
        self.assertIn("rejected:", result)

    def test_malformed_payloads(self) -> None:
        self.assertTrue(self.daemon.unset("not json").startswith("error: invalid JSON"))
        self.assertTrue(self.daemon.unset('{"a": 1}').startswith("error: unset payload"))
        self.assertTrue(self.daemon.unset("[1, 2]").startswith("error: unset payload"))

    def test_unset_is_exposed_over_dbus(self) -> None:
        from theater_mode.service import make_handler

        invocation = MagicMock()
        handler = make_handler(self.daemon, lambda _sig, args: args)
        params = MagicMock()
        params.unpack.return_value = ('["effect.dim_factor"]',)
        handler(None, "s", "/p", "i", "Unset", params, invocation)
        invocation.return_value.assert_called_once()
        self.assertIn("unset 0 keys", invocation.return_value.call_args[0][0][0])


class TestClientUnsetRouting(unittest.TestCase):
    def _run(self, argv: list[str], responses: dict[str, str]) -> tuple[int, str, MagicMock]:
        call = MagicMock(side_effect=lambda method, *a: responses.get(method, ""))
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = client_main(argv, call_dbus=call)
        return code, out.getvalue(), call

    def test_sends_key_list_and_reports_the_new_value(self) -> None:
        resolved = {"effect": {"dim_factor": 0.75}, "provenance": {}, "outputs": {}}
        code, out, call = self._run(
            ["config", "unset", "effect.dim_factor"],
            {"Unset": "unset 1 keys", "GetResolved": json.dumps(resolved)},
        )
        self.assertEqual(code, 0)
        self.assertEqual(call.call_args_list[0][0], ("Unset", '["effect.dim_factor"]'))
        self.assertIn("effect.dim_factor is now 0.75", out)

    def test_accepts_several_keys_at_once(self) -> None:
        resolved = {"effect": {"dim_factor": 0.75, "mode": "dim"}, "provenance": {}, "outputs": {}}
        code, _out, call = self._run(
            ["config", "unset", "effect.dim_factor", "effect.mode"],
            {"Unset": "unset 2 of 2 keys", "GetResolved": json.dumps(resolved)},
        )
        self.assertEqual(code, 0)
        self.assertEqual(
            json.loads(call.call_args_list[0][0][1]), ["effect.dim_factor", "effect.mode"]
        )

    def test_rejected_key_exits_nonzero(self) -> None:
        code, _out, call = self._run(
            ["config", "unset", "effect.bogus"],
            {"Unset": "error: nothing to unset; rejected: Unknown configuration key"},
        )
        self.assertEqual(code, 1)
        # It must not go on to ask for the resolved config after a failure.
        self.assertEqual([c[0][0] for c in call.call_args_list], ["Unset"])


if __name__ == "__main__":
    unittest.main()
