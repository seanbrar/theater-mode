#!/usr/bin/env bash
#
# Inner half of tools/nested/nested-session.sh. Runs inside the mount namespace and the
# private D-Bus session; not intended to be invoked directly.

set -euo pipefail

die() { printf '\033[36m[nested]\033[0m \033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }
note() { printf '\033[36m[nested]\033[0m %s\n' "$*"; }

process_running() {
    local pid=$1 state
    kill -0 "$pid" 2>/dev/null || return 1
    if [ -f "/proc/$pid/status" ]; then
        grep -q '^State:[[:space:]]*Z' "/proc/$pid/status" 2>/dev/null && return 1
        return 0
    fi
    state="$(ps -o stat= -p "$pid" 2>/dev/null || true)"
    [[ "$state" != Z* ]]
}

tail_log() {
    local file=$1 label=$2
    if [ ! -s "$file" ]; then
        printf '\033[36m[nested]\033[0m %s log is empty (%s)\n' "$label" "$file" >&2
        return 0
    fi
    printf '\033[36m[nested]\033[0m last lines of %s log (%s):\n' "$label" "$file" >&2
    tail -15 "$file" | sed 's/^/  /' >&2
}

summarize_status() {
    local file=$1
    if [ ! -s "$file" ]; then
        printf '\033[36m[nested]\033[0m last status unavailable\n' >&2
        return 0
    fi
    python3 - "$file" <<'PY' >&2
import json
import sys

try:
    status = json.loads(open(sys.argv[1], encoding="utf-8").read())
except (OSError, json.JSONDecodeError) as error:
    print(f"\033[36m[nested]\033[0m last status unavailable: {error}")
    sys.exit()
games = status.get("games") or []
if games:
    tracked = ", ".join(
        f"AppID {game.get('appid')} on {game.get('output') or 'unknown'}"
        f" ({'fullscreen' if game.get('fullscreen') else 'not fullscreen'})"
        for game in games
    )
else:
    tracked = (
        f"no tracked games ({status.get('tracked_windows', 0)} windows tracked, "
        f"detector silent for {status.get('detector_silence_seconds', '?')}s)"
    )
active = status.get("active_output") or "none"
dimmed = ", ".join(status.get("affected_outputs") or []) or "none"
print(f"\033[36m[nested]\033[0m last status: {tracked}; active: {active}; dimmed: {dimmed}")
PY
}

fail_with_log() {
    local message=$1 file=$2 label=$3
    printf '\033[36m[nested]\033[0m \033[31merror:\033[0m %s\n' "$message" >&2
    if [ "$#" -ge 4 ]; then
        summarize_status "$4"
    fi
    tail_log "$file" "$label"
    exit 1
}

RUN_DIR="${THEATER_NESTED_RUN_DIR:?}"
MODE="${THEATER_NESTED_MODE:?}"
HEADLESS="${THEATER_NESTED_HEADLESS:-0}"
APPID="${THEATER_NESTED_APPID:?}"
GAME_CMD="${THEATER_NESTED_GAME_CMD:-}"
OUTPUT_COUNT="${THEATER_NESTED_OUTPUT_COUNT:?}"
GEOMETRY="${THEATER_NESTED_GEOMETRY:?}"
XWAYLAND="${THEATER_NESTED_XWAYLAND:-0}"
TIMEOUT="${THEATER_NESTED_TIMEOUT:?}"
SHOWCASE="${THEATER_NESTED_SHOWCASE:-}"
CONNECTORS="${THEATER_NESTED_CONNECTORS:?}"
REPO_DIR="${THEATER_NESTED_REPO_DIR:?}"

SOCKET="theater-nested-$$"
WIDTH="${GEOMETRY%x*}"
HEIGHT="${GEOMETRY#*x}"

cd "$REPO_DIR"

kwin_args=(
    --socket "$SOCKET"
    --width "$WIDTH"
    --height "$HEIGHT"
    --output-count "$OUTPUT_COUNT"
    --no-lockscreen
)
# Xwayland is off by default because a nested instance can fail to claim an X11 display
# number. The harness does not currently discover that display for the fake game.
if [ "$XWAYLAND" -eq 1 ]; then
    kwin_args+=(--xwayland)
fi
if [ "$HEADLESS" -eq 1 ]; then
    kwin_args+=(--virtual)
else
    [ -n "${WAYLAND_DISPLAY:-}" ] || die "no host WAYLAND_DISPLAY; use --headless"
    kwin_args+=(--wayland-display "$WAYLAND_DISPLAY")
fi

# Qt logs to the journal by default, which hides the detector's own messages behind the
# host's unit filtering. Forcing stderr keeps everything in one file.
export QT_FORCE_STDERR_LOGGING=1
export QT_LOGGING_RULES="kwin_scripting.debug=true;js.debug=true"

kwin_wayland "${kwin_args[@]}" > "$RUN_DIR/kwin.log" 2>&1 &
KWIN_PID=$!
cleanup() { kill "$KWIN_PID" "${DAEMON_PID:-}" "${GAME_PID:-}" 2>/dev/null || true; }
trap cleanup EXIT

deadline=$((SECONDS + TIMEOUT))
while [ "$SECONDS" -lt "$deadline" ]; do
    [ -S "$XDG_RUNTIME_DIR/$SOCKET" ] && break
    if ! process_running "$KWIN_PID"; then
        fail_with_log "nested compositor exited" "$RUN_DIR/kwin.log" "KWin"
    fi
    sleep 0.25
done
if [ ! -S "$XDG_RUNTIME_DIR/$SOCKET" ]; then
    fail_with_log "nested compositor never came up" "$RUN_DIR/kwin.log" "KWin"
fi
sleep 2
export WAYLAND_DISPLAY="$SOCKET"
note "nested compositor up on \$WAYLAND_DISPLAY=$SOCKET"

PYTHONPATH=src \
THEATER_DEV_CONFIG_OVERRIDE="$RUN_DIR/config.toml" \
THEATER_DEV_FORCE_ART_DIR="$RUN_DIR/art" \
THEATER_DEV_VERBOSE=1 \
    ./bin/theater-moded > "$RUN_DIR/daemon.log" 2>&1 &
DAEMON_PID=$!

daemon_ready=0
deadline=$((SECONDS + TIMEOUT))
while [ "$SECONDS" -lt "$deadline" ]; do
    if ! process_running "$DAEMON_PID"; then
        fail_with_log "daemon exited" "$RUN_DIR/daemon.log" "daemon"
    fi
    remaining=$((deadline - SECONDS))
    if PYTHONPATH=src timeout --foreground "$remaining" \
        ./bin/theater-mode status --json > "$RUN_DIR/status.json" 2>/dev/null; then
        daemon_ready=1
        break
    fi
    [ "$SECONDS" -lt "$deadline" ] || break
    sleep 0.25
done
if [ "$daemon_ready" -ne 1 ]; then
    fail_with_log "daemon did not become ready" "$RUN_DIR/daemon.log" "daemon"
fi

if [ "$MODE" = interactive ] && [ -z "$SHOWCASE" ]; then
    note "daemon sees:"
    PYTHONPATH=src ./bin/theater-mode outputs | sed 's/^/    /'
fi

if [ -n "$SHOWCASE" ]; then
    PYTHONPATH=src "$REPO_DIR/tools/showcase.py" --suite "$SHOWCASE" --appid "$APPID"
    exit 0
fi

if [ -z "$GAME_CMD" ]; then
    for candidate in kwrite kate konsole; do
        if command -v "$candidate" >/dev/null 2>&1; then
            if [ "$candidate" = "konsole" ]; then
                GAME_CMD="konsole --hold -e true"
            else
                GAME_CMD="$candidate $RUN_DIR/fake-game.txt"
            fi
            break
        fi
    done
    [ -n "$GAME_CMD" ] || die "no suitable KDE window found (install kwrite, kate, or konsole, or pass --game)"
fi

note "launching fake game (appid $APPID): $GAME_CMD"
read -r -a game_argv <<< "$GAME_CMD"
SteamGameId="$APPID" "${game_argv[@]}" > "$RUN_DIR/game.log" 2>&1 &
GAME_PID=$!

if [ "$MODE" = interactive ]; then
    note "session is live. Ctrl-C here to tear everything down."
    note "  logs: $RUN_DIR/{kwin,daemon,game}.log"
    wait "$KWIN_PID"
    exit 0
fi

effect_ready=0
deadline=$((SECONDS + TIMEOUT))
while [ "$SECONDS" -lt "$deadline" ]; do
    if ! process_running "$DAEMON_PID"; then
        fail_with_log "daemon exited during effect activation" "$RUN_DIR/daemon.log" "daemon"
    fi
    if ! process_running "$GAME_PID"; then
        fail_with_log "game window exited during effect activation" "$RUN_DIR/game.log" "game"
    fi
    remaining=$((deadline - SECONDS))
    if PYTHONPATH=src timeout --foreground "$remaining" \
        ./bin/theater-mode status --json > "$RUN_DIR/status.json" 2>/dev/null \
        && python3 - "$RUN_DIR/status.json" 2>/dev/null <<'PY'
import json
import sys

status = json.loads(open(sys.argv[1], encoding="utf-8").read())
sys.exit(0 if status.get("active_output") is not None else 1)
PY
    then
        effect_ready=1
        break
    fi
    [ "$SECONDS" -lt "$deadline" ] || break
    sleep 0.25
done
if [ "$effect_ready" -ne 1 ]; then
    fail_with_log \
        "effect did not activate within ${TIMEOUT}s" \
        "$RUN_DIR/daemon.log" \
        "daemon" \
        "$RUN_DIR/status.json"
fi

python3 - "$RUN_DIR/status.json" "$CONNECTORS" <<'PY'
import json
import sys

status = json.loads(open(sys.argv[1], encoding="utf-8").read())
expected = sorted(sys.argv[2].splitlines())
failures = []

if sorted(status["outputs"]) != expected:
    failures.append(f"outputs {status['outputs']} != profile connectors {expected}")

active = status["active_output"]
if active is None:
    failures.append("no active output: the detector never reported a game window")
elif active not in expected:
    failures.append(f"active output {active!r} is not one of {expected}")

if len(expected) > 1:
    affected = sorted(status["affected_outputs"])
    should_dim = sorted(name for name in expected if name != active)
    if affected != should_dim:
        failures.append(f"dimmed {affected}, expected {should_dim}")
    if not status["effect_process_running"]:
        failures.append("dimmer helper is not running")
elif status["affected_outputs"]:
    failures.append(f"single-output profile dimmed {status['affected_outputs']}")

if failures:
    prefix = "\033[36m[nested]\033[0m \033[31merror:\033[0m"
    print("\n".join(f"{prefix} {line}" for line in failures), file=sys.stderr)
    print(
        f"\033[36m[nested]\033[0m status payload kept at {sys.argv[1]}",
        file=sys.stderr,
    )
    sys.exit(1)
if len(expected) == 1:
    print("\033[36m[nested]\033[0m \033[32mPassed:\033[0m effect remained inert on one display")
else:
    print(
        f"\033[36m[nested]\033[0m \033[32mPassed:\033[0m "
        f"effect applied (active: {active}; dimmed: {', '.join(affected)})"
    )
PY
