#!/usr/bin/env python3
"""Walk through configuration cases that require visual inspection."""

from __future__ import annotations

import argparse
import json
import re
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from theater_mode.client import _call_dbus_method  # noqa: E402
from theater_mode.config.dev import get_dev_config  # noqa: E402
from theater_mode.constants import STEAM_LIBRARY_CACHES  # noqa: E402

SUITE_DESCRIPTIONS = {
    "flat": "flat overlays across the useful dimming range",
    "artwork": "artwork rendering at several dimming levels",
    "compare": "direct A/B toggles between flat overlays and artwork",
    "placement": "flat and artwork surfaces above and below windows",
    "outputs": "move the simulated game across every display",
    "curves": "all transition curves at visible and extreme durations",
    "overrides": "per-output settings overriding global defaults",
}


@dataclass(frozen=True, slots=True)
class Step:
    """One configuration and simulated game-output combination."""

    title: str
    updates: dict[str, Any]
    game_output: str
    reset_before: bool = False


def call(method: str, *args: str) -> str:
    """Call the daemon and reject application-level errors."""
    try:
        result = _call_dbus_method(method, *args)
    except SystemExit as error:
        raise RuntimeError("the daemon connection failed") from error
    if result.startswith("error") or "rejected:" in result:
        raise RuntimeError(result)
    return result


def daemon_status() -> dict[str, Any]:
    """Return the daemon status object after validating its top-level shape."""
    try:
        status = json.loads(call("Status"))
        if not isinstance(status, dict):
            raise TypeError("response is not an object")
        return status
    except (json.JSONDecodeError, TypeError) as error:
        raise RuntimeError(f"invalid Status response: {error}") from error


def active_outputs() -> list[str]:
    """Return connector names KScreen reports as connected and enabled."""
    try:
        result = subprocess.run(
            ["kscreen-doctor", "--json"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        configuration = json.loads(result.stdout)
        if not isinstance(configuration, dict) or not isinstance(
            configuration.get("outputs"), list
        ):
            raise TypeError("response has no output list")
        outputs: list[str] = []
        for index, output in enumerate(configuration["outputs"]):
            if not isinstance(output, dict):
                raise TypeError(f"output {index} is not an object")
            connected = output.get("connected")
            enabled = output.get("enabled")
            if not isinstance(connected, bool) or not isinstance(enabled, bool):
                raise TypeError(f"output {index} has invalid connection state")
            if not connected or not enabled:
                continue
            name = output.get("name")
            if not isinstance(name, str) or not name:
                raise TypeError(f"active output {index} has no display name")
            outputs.append(name)
        return sorted(outputs)
    except FileNotFoundError as error:
        raise RuntimeError("kscreen-doctor is required to query active displays") from error
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("kscreen-doctor did not respond within 5 seconds") from error
    except subprocess.CalledProcessError as error:
        detail = error.stderr.strip() or f"status {error.returncode}"
        raise RuntimeError(f"kscreen-doctor failed: {detail}") from error
    except (json.JSONDecodeError, TypeError) as error:
        raise RuntimeError(f"invalid kscreen-doctor response: {error}") from error


def session_preview_keys() -> list[str]:
    """Return configuration keys currently supplied by the session preview layer."""
    try:
        resolved = json.loads(call("GetResolved"))
        if not isinstance(resolved, dict):
            raise TypeError("response is not an object")
        provenance = resolved.get("provenance")
        if not isinstance(provenance, dict):
            raise TypeError("provenance is not an object")
        keys: list[str] = []
        for key, source in provenance.items():
            if not isinstance(key, str) or not isinstance(source, dict):
                raise TypeError("provenance entry has an invalid shape")
            if source.get("layer") == "session":
                keys.append(key)
        return sorted(keys)
    except (json.JSONDecodeError, TypeError) as error:
        raise RuntimeError(f"invalid GetResolved response: {error}") from error


def detect_appid() -> str | None:
    """Find an AppID with locally cached library artwork."""
    candidates: set[str] = set()
    override = get_dev_config().force_art_dir
    caches = (override,) if override is not None else STEAM_LIBRARY_CACHES
    for cache in caches:
        if not cache.is_dir():
            continue
        try:
            for artwork in cache.rglob("library_hero.jpg"):
                appid = artwork.relative_to(cache).parts[0]
                if appid.isdigit() and int(appid) > 0:
                    candidates.add(appid)
        except (IndexError, OSError, ValueError):
            continue
    return min(candidates, key=int) if candidates else None


def game_title_for_appid(appid: str) -> str | None:
    """Return the game's name from its Steam appmanifest, or None if unavailable."""
    for cache in STEAM_LIBRARY_CACHES:
        # Each cache is <steam root>/appcache/librarycache.
        manifest = cache.parent.parent / "steamapps" / f"appmanifest_{appid}.acf"
        if manifest.is_file():
            try:
                match = re.search(r'"name"\s+"([^"]+)"', manifest.read_text(errors="ignore"))
                if match:
                    return match.group(1)
            except OSError:
                pass
    return None


def build_suites(outputs: list[str], game_output: str) -> dict[str, list[Step]]:
    """Build the visual cases supported by the active display topology."""
    secondary = [output for output in outputs if output != game_output]

    def step(title: str, **updates: Any) -> Step:
        return Step(title, updates, game_output)

    suites = {
        "flat": [
            step(
                f"Flat overlay at {dimming:.0%} dimming",
                **{
                    "effect.artwork": False,
                    "effect.placement": "over_windows",
                    "effect.dimming": dimming,
                    "transition.duration": 0.5,
                    "transition.curve": "sine",
                },
            )
            for dimming in (0.2, 0.5, 0.85, 1.0, 0.5)
        ],
        "artwork": [
            step(
                f"Artwork at {dimming:.0%} dimming",
                **{
                    "effect.artwork": True,
                    "effect.placement": "over_windows",
                    "effect.dimming": dimming,
                    "transition.duration": 0.5,
                    "transition.curve": "sine",
                },
            )
            for dimming in (0.35, 0.65, 0.85)
        ],
        "compare": [
            step(
                f"{'Artwork' if artwork else 'Flat overlay'} at {dimming:.0%} dimming",
                **{
                    "effect.artwork": artwork,
                    "effect.placement": "over_windows",
                    "effect.dimming": dimming,
                    "transition.duration": 0.5,
                    "transition.curve": "sine",
                },
            )
            for dimming in (0.85, 0.5)
            for artwork in (False, True)
        ],
        "placement": [
            step(
                f"{placement}, {'artwork' if artwork else 'flat'}",
                **{
                    "effect.artwork": artwork,
                    "effect.placement": placement,
                    "effect.dimming": 0.4 if placement == "behind_windows" else 0.8,
                    "transition.duration": 0.5,
                },
            )
            for placement in ("over_windows", "behind_windows")
            for artwork in (False, True)
        ],
        "outputs": [
            Step(
                f"Effect on {', '.join(o for o in outputs if o != output)}",
                {
                    "effect.artwork": False,
                    "effect.placement": "over_windows",
                    "effect.dimming": 0.8,
                    "transition.duration": 0.5,
                },
                output,
            )
            for output in outputs
        ],
        "curves": [
            Step(
                f"{curve} fade over {duration}s",
                {
                    "effect.artwork": False,
                    "effect.placement": "over_windows",
                    "effect.dimming": 0.85,
                    "transition.duration": duration,
                    "transition.curve": curve,
                },
                game_output,
                reset_before=True,
            )
            for curve, duration in (
                ("sine", 1.5),
                ("quad", 1.5),
                ("cubic", 1.5),
                ("linear", 1.5),
                ("sine", 0.4),
                ("sine", 3.0),
            )
        ],
    }

    if len(secondary) >= 2:
        first, second = secondary[:2]
        suites["overrides"] = [
            step(
                f"Per-output settings on {first} and {second}",
                **{
                    "effect.artwork": False,
                    "effect.dimming": 0.85,
                    f"outputs.{first}.artwork": True,
                    f"outputs.{first}.dimming": 0.4,
                    f"outputs.{second}.artwork": False,
                    f"outputs.{second}.dimming": 0.9,
                    "transition.duration": 0.5,
                },
            )
        ]
    elif secondary:
        first = secondary[0]
        suites["overrides"] = [
            step(
                f"Per-output override on {first}",
                **{
                    "effect.artwork": False,
                    "effect.dimming": 0.85,
                    f"outputs.{first}.artwork": True,
                    f"outputs.{first}.dimming": 0.4,
                    "transition.duration": 0.5,
                },
            )
        ]

    return suites


def show_step(index: int, total: int, step: Step, show_game_output: bool) -> None:
    """Print the state a contributor should inspect."""
    game = f" (game on {step.game_output})" if show_game_output else ""
    print(f"\n[{index}/{total}] {step.title}{game}")
    for key, value in step.updates.items():
        print(f"  {key} = {json.dumps(value)}")


def advance(prompt: str) -> str:
    """Return the navigation action requested by the contributor.

    Returns 'next', 'prev', 'replay', or 'quit'. End of input counts as 'quit', so
    feeding the prompt a fixed number of newlines walks that many cases and exits.
    """
    while True:
        try:
            command = input(prompt).strip().lower()
            if command in ("", "n", "next"):
                return "next"
            if command in ("p", "prev", "back"):
                return "prev"
            if command in ("r", "replay"):
                return "replay"
            if command in ("q", "quit"):
                return "quit"
        except EOFError:
            print()
            return "quit"


def run(
    steps: list[Step],
    appid: str,
    interval: float | None,
    dry_run: bool,
    suite_name: str | None = None,
) -> int:
    """Apply each case and restore daemon state on exit.

    Returns the process exit status. Restoration runs whether the walk finished, was
    stopped, interrupted, or failed. An incomplete restoration returns a failure status.
    """
    changed = False
    active_output: str | None = None
    duration = 2.0
    status = 0
    title = game_title_for_appid(appid) if not dry_run and appid != "0" else None
    index = 0
    total = len(steps)
    replaying = False
    completed = False
    shown_output = steps[0].game_output if steps else None
    heading = "Showcase" if suite_name is None else f"Showcase {suite_name}"
    mode_tag = " (dry run)" if dry_run else ""
    context = [f"{total} {'step' if total == 1 else 'steps'}"]
    if shown_output is not None:
        context.append(f"game on {shown_output}")
    if not dry_run:
        target = f"AppID {appid}" if title is None else f"AppID {appid} ({title})"
        context.append(target)
    print(f"{heading}{mode_tag}: {'; '.join(context)}")
    try:
        while 0 <= index < total:
            case = steps[index]
            show_step(index + 1, total, case, case.game_output != shown_output)
            shown_output = case.game_output
            if not dry_run:
                # Re-sending identical values animates the helper from a value to itself,
                # so a replay has to clear first or the screen never moves.
                if case.reset_before or replaying:
                    call("Clear")
                    time.sleep(duration + 0.1)
                    active_output = None
                changed = True
                call("Preview", json.dumps(case.updates))
                if active_output != case.game_output:
                    call("Simulate", appid, case.game_output)
                    active_output = case.game_output
                duration = float(case.updates.get("transition.duration", duration))
            replaying = False

            if interval is None:
                action = advance("  [Enter: next, p: prev, r: replay, q: quit]: ")
                if action == "quit":
                    break
                if action == "prev":
                    index = max(0, index - 1)
                elif action == "replay":
                    replaying = True
                else:
                    index += 1
            else:
                time.sleep(interval)
                index += 1
        completed = index >= total
    except KeyboardInterrupt:
        status = 130
    except RuntimeError as error:
        print(f"\nshowcase.py: {error}", file=sys.stderr)
        status = 1
    finally:
        cleanup_failed = False
        if changed:
            try:
                call("Clear")
            except RuntimeError as error:
                print(f"showcase.py: could not clear the simulation: {error}", file=sys.stderr)
                cleanup_failed = True
            try:
                call("RevertPreview")
            except RuntimeError as error:
                print(f"showcase.py: could not discard preview settings: {error}", file=sys.stderr)
                cleanup_failed = True
        if cleanup_failed:
            status = 1
            print(
                "showcase.py: cleanup was incomplete; check the daemon before continuing",
                file=sys.stderr,
            )
        else:
            outcome = "finished" if completed else "stopped"
            restoration = "; restored displays and discarded preview settings" if changed else ""
            print(f"\nShowcase {outcome}{restoration}.")
    return status


def main() -> int:
    """Parse options and run the selected visual suites."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", default="all", help="Suite name, or 'all'")
    parser.add_argument("--game-output", help="Display carrying the simulated game")
    parser.add_argument("--appid", help="Steam AppID used for artwork")
    parser.add_argument("--interval", type=float, help="Advance automatically after N seconds")
    parser.add_argument(
        "--output",
        action="append",
        dest="offline_outputs",
        metavar="NAME",
        help="Use this display in --dry-run instead of contacting the daemon (repeatable)",
    )
    parser.add_argument("--list", action="store_true", help="List suite names and exit")
    parser.add_argument("--dry-run", action="store_true", help="Print cases without applying them")
    args = parser.parse_args()

    if args.list:
        for name, description in SUITE_DESCRIPTIONS.items():
            print(f"{name:<10} {description}")
        return 0
    if args.interval is not None and args.interval <= 0:
        parser.error("--interval must be greater than zero")
    if args.offline_outputs and not args.dry_run:
        parser.error("--output is only valid with --dry-run")
    if args.appid is not None and (not args.appid.isdigit() or int(args.appid) <= 0):
        parser.error("--appid must be a positive integer")
    if args.offline_outputs and any(not output for output in args.offline_outputs):
        parser.error("display names passed with --output must not be empty")

    status: dict[str, Any] | None = None
    if args.offline_outputs:
        outputs = args.offline_outputs
    else:
        try:
            outputs = active_outputs()
            if not args.dry_run:
                status = daemon_status()
        except RuntimeError as err:
            print(f"showcase.py: {err}", file=sys.stderr)
            return 1
    if len(set(outputs)) != len(outputs):
        parser.error("display names must be unique")
    if len(outputs) < 2:
        parser.error(
            "at least two enabled outputs are required; pass --output twice with "
            "--dry-run, or run tools/nested/nested-session.sh --showcase all"
        )
    if args.game_output and args.game_output not in outputs:
        parser.error(f"unknown game output {args.game_output!r}; choose from {', '.join(outputs)}")
    game_output = args.game_output or outputs[0]

    if not args.dry_run:
        try:
            if status is None:
                raise RuntimeError("daemon status was not loaded")
            games = status.get("games")
            if not isinstance(games, list):
                raise RuntimeError("invalid Status response: games is not a list")
            previews = session_preview_keys()
        except RuntimeError as err:
            print(f"showcase.py: {err}", file=sys.stderr)
            return 1
        if games:
            parser.error(
                "the daemon is already tracking a game; close it before running the showcase"
            )
        # RevertPreview clears the whole session layer, so the showcase cannot safely borrow it.
        if previews:
            parser.error(
                "session preview settings are already active; run "
                f"'theater-mode config revert-preview' first ({', '.join(previews)})"
            )

    appid = args.appid or ("0" if args.dry_run else detect_appid())
    if appid is None:
        parser.error("no cached Steam artwork found; pass --appid")

    suites = build_suites(outputs, game_output)
    if args.suite != "all" and args.suite not in suites:
        parser.error(f"unknown suite {args.suite!r}; choose from {', '.join(suites)}")

    selected = (
        [case for cases in suites.values() for case in cases]
        if args.suite == "all"
        else suites[args.suite]
    )
    return run(selected, appid, args.interval, args.dry_run, args.suite)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    sys.exit(main())
