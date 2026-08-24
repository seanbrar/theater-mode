#!/usr/bin/env bash
#
# Inner half of tools/nested/nested-session.sh. Runs inside the mount namespace and the
# private D-Bus session; not intended to be invoked directly.

set -euo pipefail

die() { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }
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
        die "nested compositor exited; see $RUN_DIR/kwin.log"
    fi
    sleep 0.25
done
[ -S "$XDG_RUNTIME_DIR/$SOCKET" ] || die "nested compositor never came up; see $RUN_DIR/kwin.log"
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
        die "daemon exited; see $RUN_DIR/daemon.log"
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
[ "$daemon_ready" -eq 1 ] || die "daemon did not become ready; see $RUN_DIR/daemon.log"

note "daemon sees:"
PYTHONPATH=src ./bin/theater-mode outputs | sed 's/^/    /'

if [ -n "$SHOWCASE" ]; then
    note "starting showcase suite: $SHOWCASE"
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
        die "daemon exited during effect activation; see $RUN_DIR/daemon.log"
    fi
    if ! process_running "$GAME_PID"; then
        die "game window exited during effect activation; see $RUN_DIR/game.log"
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
[ "$effect_ready" -eq 1 ] || die "effect did not activate within ${TIMEOUT}s"

note "status:"
sed 's/^/    /' "$RUN_DIR/status.json"

python3 - "$RUN_DIR/status.json" "$CONNECTORS" <<'PY'
import json
import sys

status = json.loads(open(sys.argv[1], encoding="utf-8").read())
expected = sorted(sys.argv[2].split("\n"))
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
    print("\n".join(f"\033[31mFAIL\033[0m {line}" for line in failures))
    sys.exit(1)
if len(expected) == 1:
    print("\033[32mPASS\033[0m effect correctly remained inert on one display")
else:
    print("\033[32mPASS\033[0m effect applied correctly")
PY
