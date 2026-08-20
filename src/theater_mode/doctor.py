"""Health checks for a theater-mode installation.

Answers "why isn't this working" in one command, so a user never has to interpret
`systemctl` output, hunt through System Settings, or read a journal to file a useful bug
report. Every check degrades to a finding rather than an exception: the daemon being
unreachable is the most common reason to run this, so nothing here may depend on it.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import platform
import re
import shutil
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from theater_mode import __version__
from theater_mode.constants import (
    ART_BINARY_NAME,
    ART_CACHE,
    DIMMER_BINARY_NAME,
    KWIN_CONFIG_FILE,
    KWIN_PLUGIN_ID,
    KWIN_SCRIPT_DIR,
    SERVICE_UNIT,
    STEAM_LIBRARY_CACHES,
)

OK = "ok"
WARN = "warn"
FAIL = "fail"

_MARKS = {OK: "  ok  ", WARN: " warn ", FAIL: " FAIL "}

# Threshold for reporting lost KWin detector contact (4 consecutive missed snapshots).
DETECTOR_SILENCE_LIMIT = 60.0


@dataclass(slots=True, frozen=True)
class Check:
    """One diagnostic result. `hint` is shown only when the check is not passing."""

    section: str
    name: str
    status: str
    detail: str
    hint: str = ""


def _tilde(path: Path | str) -> str:
    """Abbreviate the user's home directory so output is safe to paste into an issue.

    Both sides are resolved first. On atomic distributions /home is a symlink to /var/home,
    so a literal prefix match reports the same file under two different identities and the
    home directory silently fails to be abbreviated.
    """
    if not path:
        return ""
    try:
        relative = Path(path).resolve().relative_to(Path.home().resolve())
        return "~" if str(relative) == "." else f"~/{relative}"
    except (OSError, ValueError):
        return str(path)


def _run(cmd: list[str], timeout: float = 5.0) -> tuple[int, str]:
    """Run a command, returning (returncode, combined output). (-1, "") if unavailable."""
    if not shutil.which(cmd[0]):
        return -1, ""
    try:
        proc = subprocess.run(  # noqa: S603
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
        return proc.returncode, (proc.stdout + proc.stderr).strip()
    except (OSError, subprocess.SubprocessError):
        return -1, ""


type Runner = Callable[[list[str]], tuple[int, str]]


def _probe(call_dbus: Callable[..., str], method: str) -> str | None:
    """Call a daemon method without letting a dead daemon abort the report."""
    with contextlib.suppress(Exception, SystemExit), contextlib.redirect_stderr(io.StringIO()):
        return call_dbus(method)
    return None


def _os_name() -> str:
    """Identify the host operating system, release, and machine architecture."""
    try:
        info = platform.freedesktop_os_release()
        name = info.get("PRETTY_NAME") or info.get("NAME") or "Linux"
        version = info.get("VERSION_ID") or info.get("VERSION") or ""
        if version and version not in name:
            name = f"{name} {version}"
        return f"{name} ({platform.machine()})"
    except (OSError, AttributeError):
        return f"{platform.system()} ({platform.machine()})"


def _check_session(env: Mapping[str, str], run: Runner) -> list[Check]:
    """Evaluate operating system, Wayland session type, desktop environment, and Plasma version."""
    section = "Session"
    checks = [Check(section, "Operating system", OK, _os_name())]

    session_type = env.get("XDG_SESSION_TYPE", "")
    desktop = env.get("XDG_CURRENT_DESKTOP", "")

    match session_type:
        case "wayland":
            checks.append(Check(section, "Session type", OK, "wayland"))
        case "":
            checks.append(
                Check(
                    section,
                    "Session type",
                    WARN,
                    "not reported",
                    "XDG_SESSION_TYPE is unset. If running from SSH, open a terminal in "
                    "your desktop session.",
                )
            )
        case other:
            checks.append(
                Check(
                    section,
                    "Session type",
                    FAIL,
                    other,
                    "theater-mode requires a Wayland session to dim secondary monitors. "
                    "Log out and select Plasma (Wayland) at your login screen.",
                )
            )

    if "gamescope" in desktop.lower():
        checks.append(
            Check(
                section,
                "Desktop",
                FAIL,
                desktop,
                "theater-mode runs in Desktop Mode, not Game Mode. "
                "Switch to Desktop Mode from the power menu.",
            )
        )
    elif "KDE" in desktop.upper():
        checks.append(Check(section, "Desktop", OK, desktop))
    else:
        checks.append(
            Check(
                section,
                "Desktop",
                FAIL,
                desktop or "not reported",
                "theater-mode requires KDE Plasma 6 (KWin) and is not supported on this "
                "desktop environment.",
            )
        )

    rc, out = run(["plasmashell", "--version"])
    if rc == 0 and out:
        version = out.split()[-1]
        match = re.search(r"(\d+)\.(\d+)", version)
        if match:
            major, minor = int(match.group(1)), int(match.group(2))
            is_supported = (major > 6) or (major == 6 and minor >= 2)
            status = OK if is_supported else FAIL
            hint = "" if status == OK else "theater-mode requires KDE Plasma 6.2 or newer."
        else:
            status = WARN
            hint = "Could not parse Plasma version."
        checks.append(Check(section, "Plasma version", status, version, hint))
    else:
        checks.append(
            Check(
                section, "Plasma version", WARN, "could not determine", "plasmashell was not found."
            )
        )

    return checks


def _helper_check(section: str, name: str, binary: Path | None, run: Runner) -> Check:
    """Resolve a helper binary, then confirm it actually executes on this machine."""
    if binary is None:
        return Check(
            section,
            name,
            FAIL,
            "not found",
            f"Reinstall theater-mode to restore {name}, or compile locally with: ./install.sh --build",
        )

    rc, out = run([str(binary), "--version"])
    if rc != 0:
        detail = out.splitlines()[0] if out else "did not run"
        hint = f"The installed {name} cannot run on this machine: {detail}"
        if "GLIBC" in out:
            hint += ". Rebuild locally with: ./install.sh --build"
        return Check(section, name, FAIL, _tilde(binary), hint)

    reported = out.split()[-1] if out else ""
    if reported != __version__:
        return Check(
            section,
            name,
            WARN,
            f"{_tilde(binary)} reports {reported or 'nothing'}",
            f"The client is {__version__}. Re-run the installer or update so both match.",
        )
    return Check(section, name, OK, f"{_tilde(binary)} ({reported})")


def _kwin_script_enabled(run: Runner) -> bool | None:
    """Read the kwinrc key System Settings toggles. None if it cannot be determined."""
    key = f"{KWIN_PLUGIN_ID}Enabled"
    rc, out = run(["kreadconfig6", "--file", "kwinrc", "--group", "Plugins", "--key", key])
    if rc == 0 and out.strip():
        return out.strip().lower() == "true"

    try:
        content = KWIN_CONFIG_FILE.read_text(encoding="utf-8", errors="replace")
        match = re.search(r"\[Plugins\][^\[]*\b" + re.escape(key) + r"\s*=\s*(\w+)", content)
        return match.group(1).lower() == "true" if match else False
    except OSError:
        return None


def _check_installation(run: Runner) -> list[Check]:
    """Verify helper binaries and KWin script presence and enablement."""
    from theater_mode.effects.dim import find_dimmer_binary
    from theater_mode.steam import find_art_binary

    section = "Installation"
    checks = [
        _helper_check(section, DIMMER_BINARY_NAME, find_dimmer_binary(), run),
        _helper_check(section, ART_BINARY_NAME, find_art_binary(), run),
    ]

    if (KWIN_SCRIPT_DIR / "metadata.json").is_file():
        checks.append(Check(section, "KWin script", OK, _tilde(KWIN_SCRIPT_DIR)))
    else:
        checks.append(
            Check(
                section,
                "KWin script",
                FAIL,
                "not installed",
                "Re-run install.sh to register the KWin detector script so "
                "theater-mode is notified when games start.",
            )
        )

    enabled = _kwin_script_enabled(run)
    if enabled is True:
        checks.append(Check(section, "KWin script enabled", OK, "yes"))
    elif enabled is False:
        checks.append(
            Check(
                section,
                "KWin script enabled",
                FAIL,
                "no",
                "Enable 'Theater Mode Detector' in System Settings → Window Management "
                "→ KWin Scripts (or re-run install.sh).",
            )
        )
    else:
        checks.append(
            Check(section, "KWin script enabled", WARN, "could not determine", "No kwinrc found.")
        )

    return checks


def _check_service(run: Runner) -> list[Check]:
    """Inspect systemd user service active status and login autostart enablement."""
    section = "Service"
    rc, active = run(["systemctl", "--user", "is-active", SERVICE_UNIT])
    if rc == -1:
        return [
            Check(
                section,
                "Unit state",
                WARN,
                "systemctl unavailable",
                "Cannot inspect the user service without systemd.",
            )
        ]

    state = active.strip() or "unknown"
    checks = [
        Check(
            section,
            "Unit state",
            OK if state == "active" else FAIL,
            state,
            "" if state == "active" else f"Start it with: systemctl --user restart {SERVICE_UNIT}",
        )
    ]

    _, enabled = run(["systemctl", "--user", "is-enabled", SERVICE_UNIT])
    enabled_state = enabled.strip() or "unknown"
    checks.append(
        Check(
            section,
            "Starts at login",
            OK if enabled_state == "enabled" else WARN,
            enabled_state,
            ""
            if enabled_state == "enabled"
            else f"Enable with: systemctl --user enable --now {SERVICE_UNIT}",
        )
    )
    return checks


def _check_daemon(call_dbus: Callable[..., str]) -> list[Check]:
    """Query daemon D-Bus responsiveness, helper process health, and detector contact."""
    section = "Daemon"
    status = _probe(call_dbus, "Status")
    checks = [
        Check(
            section,
            "D-Bus connection",
            OK if status is not None else FAIL,
            "responding" if status is not None else "no response",
            ""
            if status is not None
            else f"The daemon is not responding. Try: systemctl --user restart {SERVICE_UNIT}",
        )
    ]

    if status is not None:
        try:
            status_data = json.loads(status)
            if status_data.get("affected_outputs") and not status_data.get(
                "effect_process_running", False
            ):
                checks.append(
                    Check(
                        section,
                        "Display effect helper",
                        FAIL,
                        "not running",
                        "The Wayland dimmer helper is not running while the effect is active. "
                        "It retries automatically; if this persists, restart the service.",
                    )
                )

            silence = status_data.get("detector_silence_seconds", 0.0)
            if silence > DETECTOR_SILENCE_LIMIT:
                checks.append(
                    Check(
                        section,
                        "KWin detector contact",
                        FAIL,
                        f"silent for {silence}s",
                        "The KWin detector script is not reporting windows. Reload it with: "
                        "busctl --user call org.kde.KWin /KWin org.kde.KWin reconfigure",
                    )
                )
            else:
                checks.append(
                    Check(section, "KWin detector contact", OK, f"active ({silence}s ago)")
                )
        except (TypeError, ValueError):
            checks.append(
                Check(
                    section,
                    "Status report",
                    FAIL,
                    "unreadable",
                    "The daemon answered with a status this version cannot read. "
                    f"Try: systemctl --user restart {SERVICE_UNIT}",
                )
            )

    # Configuration diagnostics: query online daemon, or fall back to offline parser
    raw = _probe(call_dbus, "GetDiagnostics") if status is not None else None
    if raw is not None:
        try:
            entries = json.loads(raw)
        except (TypeError, ValueError):
            entries = []
        errors = sum(1 for d in entries if str(d.get("severity", "")).lower() == "error")
        warnings = len(entries) - errors
        if errors:
            checks.append(
                Check(
                    section,
                    "Configuration",
                    FAIL,
                    f"{errors} error(s), {warnings} warning(s)",
                    "Run: theater-mode config diagnostics",
                )
            )
        elif warnings:
            checks.append(
                Check(
                    section,
                    "Configuration",
                    WARN,
                    f"{warnings} warning(s)",
                    "Run: theater-mode config diagnostics",
                )
            )
        else:
            checks.append(Check(section, "Configuration", OK, "no problems reported"))
    else:
        try:
            from theater_mode.config.loader import load_resolved_config

            _, diags = load_resolved_config()
            errors = sum(1 for d in diags if getattr(d, "severity", "") == "error")
            warnings = len(diags) - errors
            if errors:
                checks.append(
                    Check(
                        section,
                        "Configuration",
                        FAIL,
                        f"{errors} error(s), {warnings} warning(s)",
                        "Run: theater-mode config diagnostics",
                    )
                )
            elif warnings:
                checks.append(
                    Check(
                        section,
                        "Configuration",
                        WARN,
                        f"{warnings} warning(s)",
                        "Run: theater-mode config diagnostics",
                    )
                )
            else:
                checks.append(Check(section, "Configuration", OK, "no problems reported"))
        except Exception as e:
            checks.append(Check(section, "Configuration", FAIL, "syntax error", str(e)))

    # Connected displays: query online daemon, or fall back to DRM sysfs
    raw_outputs = _probe(call_dbus, "GetOutputs") if status is not None else None
    outputs: list[dict[str, object]] = []
    if raw_outputs:
        try:
            outputs = json.loads(raw_outputs)
        except (TypeError, ValueError):
            outputs = []

    if not outputs and status is None:
        try:
            from theater_mode.display.drm import connected_outputs

            outputs = [{"connector": c} for c in sorted(connected_outputs())]
        except Exception:
            outputs = []

    count = len(outputs)
    if count >= 2:
        names = ", ".join(str(o.get("connector", "?")) for o in outputs[:4])
        checks.append(Check(section, "Displays", OK, f"{count} connected ({names})"))
    elif count == 1:
        checks.append(
            Check(
                section,
                "Displays",
                WARN,
                "1 connected",
                "theater-mode dims the displays a game is not on, so a single display "
                "leaves nothing to do.",
            )
        )
    else:
        checks.append(Check(section, "Displays", WARN, "none reported"))

    return checks


def _check_artwork() -> list[Check]:
    """Verify accessibility of Steam library caches and the rendered artwork cache directory."""
    section = "Artwork"
    checks: list[Check] = []

    found = next((c for c in STEAM_LIBRARY_CACHES if c.is_dir()), None)
    if found is None:
        checks.append(
            Check(
                section,
                "Steam library cache",
                WARN,
                "not found",
                "Displays will dim to a plain dark screen. Open a game's page in the "
                "Steam library once so Steam downloads its artwork.",
            )
        )
    else:
        try:
            entries = sum(1 for _ in found.iterdir())
        except OSError:
            entries = 0
        checks.append(
            Check(section, "Steam library cache", OK, f"{_tilde(found)} ({entries} entries)")
        )

    probe = ART_CACHE if ART_CACHE.is_dir() else ART_CACHE.parent
    if os.access(probe, os.W_OK):
        checks.append(Check(section, "Artwork cache", OK, _tilde(ART_CACHE)))
    else:
        checks.append(
            Check(
                section,
                "Artwork cache",
                FAIL,
                f"{_tilde(ART_CACHE)} is not writable",
                "Rendered artwork cannot be cached, so displays will dim to plain black.",
            )
        )
    return checks


def run_checks(
    call_dbus: Callable[..., str],
    env: Mapping[str, str] | None = None,
    run: Runner | None = None,
) -> list[Check]:
    """Collect every diagnostic. Never raises: a failure to check is itself a finding."""
    env = os.environ if env is None else env
    run = _run if run is None else run
    return [
        *_check_session(env, run),
        *_check_installation(run),
        *_check_service(run),
        *_check_daemon(call_dbus),
        *_check_artwork(),
    ]


def format_report(checks: list[Check]) -> str:
    """Render the checks grouped by section, with hints only where something is wrong."""
    lines: list[str] = []
    width = max((len(c.name) for c in checks), default=0)
    current = ""
    for check in checks:
        if check.section != current:
            current = check.section
            lines.append(f"\n{current}")
        lines.append(f"  [{_MARKS[check.status]}] {check.name.ljust(width)}  {check.detail}")
        if check.hint and check.status != OK:
            indent = " " * (width + 13)
            lines.append(f"{indent}{check.hint}")

    failures = sum(1 for c in checks if c.status == FAIL)
    warnings = sum(1 for c in checks if c.status == WARN)
    lines.append("")
    if failures:
        lines.append(f"{failures} problem(s) found. Address the FAIL lines above first.")
    elif warnings:
        lines.append(f"No blocking problems. {warnings} thing(s) worth a look.")
    else:
        lines.append("Everything checks out.")
    return "\n".join(lines).lstrip("\n")


def to_json(checks: list[Check]) -> str:
    """Serialize diagnostic checks as a JSON string."""
    return json.dumps(
        [
            {
                "section": c.section,
                "name": c.name,
                "status": c.status,
                "detail": c.detail,
                "hint": c.hint,
            }
            for c in checks
        ],
        indent=2,
    )


def exit_code(checks: list[Check]) -> int:
    """Return 1 if any check failed, or 0 when all checks pass or only warn."""
    return 1 if any(c.status == FAIL for c in checks) else 0
