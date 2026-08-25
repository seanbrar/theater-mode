#!/usr/bin/env bash
#
# Run theater-mode against a nested Plasma compositor with synthetic displays.
#
# Starts kwin_wayland windowed on the host with N outputs, bind-mounts a synthetic
# /sys/class/drm matching those outputs, and runs the repository's daemon, detector, and
# helpers against them on a private D-Bus. Nothing here reads or writes the live session:
# every XDG directory is redirected into a scratch tree. A passing run removes that tree.
# A failing run leaves it behind, and the log paths in its output stay valid.
#
# See tools/nested/README.md for what this does and does not cover.

set -euo pipefail

die() { printf '\033[36m[nested]\033[0m \033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }
note() { printf '\033[36m[nested]\033[0m %s\n' "$*"; }

TOOLS_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd -- "$TOOLS_DIR/../.." && pwd)"

PROFILE=dual
GEOMETRY=1280x720
APPID=440
GAME_CMD=""
CONFIG_FILE=""
MODE=interactive
HEADLESS=0
KEEP=0
XWAYLAND=0
TIMEOUT=15
SHOWCASE=""

usage() {
    cat <<'USAGE'
Usage: tools/nested/nested-session.sh [options]

  --profile NAME     display profile from tools/nested/profiles (default: dual)
  --geometry WxH     size of each nested output window (default: 1280x720)
  --appid ID         Steam AppID the fake game reports (default: 440)
  --game CMD         command to run as the fake game (default: a Plasma text editor)
  --config FILE      seed the private daemon with this theater-mode config
  --xwayland         start Xwayland alongside the nested compositor
  --check            run unattended, assert the effect applied, exit non-zero on failure
  --headless         render to a virtual framebuffer instead of windows on your desktop
  --showcase SUITE   inspect a showcase suite in the nested displays
  --timeout SECONDS  startup and effect deadline (default: 15)
  --keep             keep the scratch tree after a passing run; a failing run keeps it
  -h, --help         show this message

The fake game needs no Steam install: steam_appid_for_window falls back to reading
SteamGameId from /proc/<pid>/environ, so any windowed process launched with that variable
set is treated as a game.
USAGE
}

require_value() {
    if [ $# -lt 2 ] || [ -z "$2" ]; then
        die "$1 requires a value"
    fi
}

while [ $# -gt 0 ]; do
    case "$1" in
        --profile=*) PROFILE="${1#*=}"; [ -n "$PROFILE" ] || die "--profile requires a value"; shift ;;
        --profile)   require_value "$@"; PROFILE=$2; shift 2 ;;
        --geometry=*) GEOMETRY="${1#*=}"; [ -n "$GEOMETRY" ] || die "--geometry requires a value"; shift ;;
        --geometry)  require_value "$@"; GEOMETRY=$2; shift 2 ;;
        --appid=*)   APPID="${1#*=}"; [ -n "$APPID" ] || die "--appid requires a value"; shift ;;
        --appid)     require_value "$@"; APPID=$2; shift 2 ;;
        --game=*)    GAME_CMD="${1#*=}"; [ -n "$GAME_CMD" ] || die "--game requires a value"; shift ;;
        --game)      require_value "$@"; GAME_CMD=$2; shift 2 ;;
        --config=*)  CONFIG_FILE="${1#*=}"; [ -n "$CONFIG_FILE" ] || die "--config requires a value"; shift ;;
        --config)    require_value "$@"; CONFIG_FILE=$2; shift 2 ;;
        --xwayland)  XWAYLAND=1; shift ;;
        --check)     MODE=check; shift ;;
        --headless)  HEADLESS=1; MODE=check; shift ;;
        --showcase=*) SHOWCASE="${1#*=}"; [ -n "$SHOWCASE" ] || die "--showcase requires a value"; shift ;;
        --showcase)  require_value "$@"; SHOWCASE=$2; shift 2 ;;
        --timeout=*) TIMEOUT="${1#*=}"; [ -n "$TIMEOUT" ] || die "--timeout requires a value"; shift ;;
        --timeout)   require_value "$@"; TIMEOUT=$2; shift 2 ;;
        --keep)      KEEP=1; shift ;;
        -h|--help)   usage; exit 0 ;;
        *)           die "unknown option: $1 (try --help)" ;;
    esac
done

[[ "$PROFILE" =~ ^[A-Za-z0-9][A-Za-z0-9_-]*$ ]] || die "invalid profile name: $PROFILE"
[[ "$GEOMETRY" =~ ^[1-9][0-9]*x[1-9][0-9]*$ ]] || die "invalid geometry: $GEOMETRY (expected WxH)"
[[ "$APPID" =~ ^[1-9][0-9]*$ ]] || die "invalid AppID: $APPID (expected positive integer)"
[[ "$TIMEOUT" =~ ^[1-9][0-9]*$ ]] || die "invalid timeout: $TIMEOUT (expected whole seconds)"
[ -z "$SHOWCASE" ] || [ "$MODE" = interactive ] \
    || die "--showcase cannot be combined with --check or --headless"
if [ -n "$SHOWCASE" ]; then
    if [ "$SHOWCASE" != all ]; then
        SHOWCASE_NAMES="$("$REPO_DIR/tools/showcase.py" --list)" \
            || die "could not read showcase suite names"
        printf '%s\n' "$SHOWCASE_NAMES" | awk '{ print $1 }' | grep -Fxq "$SHOWCASE" \
            || die "unknown showcase suite: $SHOWCASE (try tools/showcase.py --list)"
    fi
fi

[ -z "$CONFIG_FILE" ] || [ -r "$CONFIG_FILE" ] || die "cannot read config file: $CONFIG_FILE"

PROFILE_PATH="$TOOLS_DIR/profiles/$PROFILE.json"
[ -f "$PROFILE_PATH" ] || die "no such profile: $PROFILE_PATH"

for tool in kwin_wayland bwrap dbus-run-session python3 timeout; do
    command -v "$tool" >/dev/null || die "$tool is required but not on PATH"
done

# The helpers must come from this checkout. The daemon would otherwise silently fall back
# to whatever is installed in ~/.local/bin, which is a different build with, historically,
# a different D-Bus name -- the detector then calls a service nobody owns and no window is
# ever reported. Build them first; see CONTRIBUTING.md for the Distrobox toolchain.
DIMMER_BIN="$REPO_DIR/src/theater_mode/dimmer/theater-dimmer"
ART_BIN="$REPO_DIR/src/theater_mode/art/theater-art"
[ -x "$DIMMER_BIN" ] || die "missing $DIMMER_BIN; run: make -C src/theater_mode/dimmer"
[ -x "$ART_BIN" ] || die "missing $ART_BIN; run: make -C src/theater_mode/art"

RUN_DIR="$(mktemp -d -t theater-nested-XXXXXX)"
SESSION_PGID=""

terminate_session() {
    # dbus-run-session returns as soon as the bus is gone, but the services the bus
    # activated -- a desktop portal, an accessibility bus, a gvfs daemon -- outlive it and
    # are reparented to the user's systemd. They stay in the session's process group, so
    # signalling the group is what actually ends a run. Without this every invocation
    # leaks a portal that goes on rewriting the scratch tree it was started with.
    [ -n "$SESSION_PGID" ] || return 0
    kill -- "-$SESSION_PGID" 2>/dev/null || true

    # Wait for the group to be gone before anything is removed. Those services write their
    # state out as they exit, so a removal that merely retries will keep losing the race:
    # the tree is deleted, then recreated by a process that had not finished dying.
    for _ in $(seq 20); do
        pgrep -g "$SESSION_PGID" >/dev/null 2>&1 || return 0
        sleep 0.25
    done
    kill -9 -- "-$SESSION_PGID" 2>/dev/null || true
    sleep 0.5
}

remove_run_dir() {
    rm -rf "$RUN_DIR" 2>/dev/null || true
}

finish() {
    terminate_session
    if [ "$KEEP" -eq 0 ]; then
        remove_run_dir
        return 0
    fi
    printf '\033[36m[nested]\033[0m scratch tree kept at %s\n' "$RUN_DIR" >&2
}
trap finish EXIT
# Exit through the EXIT trap on an interrupt, so Ctrl-C in interactive mode tears the
# whole session down rather than orphaning it.
trap 'exit 130' INT TERM

note "profile: $PROFILE"

# Synthetic sysfs, and the connector list the daemon is expected to report back. The
# connector prefix has to match the compositor backend about to be started: KWin names
# windowed outputs WL-N and virtual-framebuffer outputs Virtual-N, and the daemon can only
# reconcile a detector report with a sysfs connector if the two agree on the name.
if [ "$HEADLESS" -eq 1 ]; then
    CONNECTOR_PREFIX="Virtual-"
else
    CONNECTOR_PREFIX="WL-"
fi
CONNECTORS="$("$TOOLS_DIR/fake-drm.py" "$PROFILE_PATH" "$RUN_DIR/drm" \
    --connector-prefix "$CONNECTOR_PREFIX" --print-connectors)"
OUTPUT_COUNT="$(printf '%s\n' "$CONNECTORS" | wc -l)"
note "synthetic outputs: $(printf '%s' "$CONNECTORS" | tr '\n' ' ')"

# Every XDG directory is redirected. XDG_DATA_HOME matters as much as XDG_CONFIG_HOME:
# KWin resolves scripts through it, so leaving it pointed at the real home is what makes
# the nested compositor load an installed detector instead of this checkout's.
export XDG_CONFIG_HOME="$RUN_DIR/config"
export XDG_DATA_HOME="$RUN_DIR/data"
export XDG_CACHE_HOME="$RUN_DIR/cache"
export XDG_STATE_HOME="$RUN_DIR/state"
mkdir -p "$XDG_CONFIG_HOME" "$XDG_DATA_HOME/kwin/scripts" "$XDG_CACHE_HOME" "$XDG_STATE_HOME"

cp -r "$REPO_DIR/kwin/theater-detect" "$XDG_DATA_HOME/kwin/scripts/"
printf '[Plugins]\ntheater-detectEnabled=true\n' > "$XDG_CONFIG_HOME/kwinrc"

# Placeholder hero art from committed fixture, so the artwork pipeline runs without
# a Steam library or Python Pillow dependency present.
mkdir -p "$RUN_DIR/art/$APPID"
cp "$REPO_DIR/tests/fixtures/artwork_reference/hero_1080p_wide_input.jpg" \
    "$RUN_DIR/art/$APPID/library_hero.jpg"

# The daemon is always pointed at $RUN_DIR/config.toml. Leaving that file absent is what
# makes an unseeded run test documented defaults rather than whatever the host user has
# configured, so copy one in only when the caller asked for it.
[ -z "$CONFIG_FILE" ] || cp "$CONFIG_FILE" "$RUN_DIR/config.toml"

export THEATER_NESTED_RUN_DIR="$RUN_DIR"
export THEATER_NESTED_MODE="$MODE"
export THEATER_NESTED_HEADLESS="$HEADLESS"
export THEATER_NESTED_APPID="$APPID"
export THEATER_NESTED_GAME_CMD="$GAME_CMD"
export THEATER_NESTED_OUTPUT_COUNT="$OUTPUT_COUNT"
export THEATER_NESTED_GEOMETRY="$GEOMETRY"
export THEATER_NESTED_XWAYLAND="$XWAYLAND"
export THEATER_NESTED_TIMEOUT="$TIMEOUT"
export THEATER_NESTED_SHOWCASE="$SHOWCASE"
export THEATER_NESTED_CONNECTORS="$CONNECTORS"
export THEATER_NESTED_REPO_DIR="$REPO_DIR"
export THEATER_DIMMER_BIN="$DIMMER_BIN"
export THEATER_ART_BIN="$ART_BIN"

# bwrap rather than `unshare -r`: mapping to root sends dbus-daemon looking for /root and
# breaks portal activation inside the session. bwrap gives the same mount namespace while
# leaving the uid alone.
#
# Not exec'd: exec would replace this shell and discard the EXIT trap, leaking the scratch
# tree into /tmp on every run.
#
# Session stderr goes to a log rather than the terminal. A private bus activates the
# desktop portals from scratch, and their startup complaints -- a document portal that
# cannot mount FUSE, an accessibility bus with no systemd to talk to -- are expected here
# and would otherwise bury the result. Failures reprint the tail.
note "starting private session"
setsid bwrap \
    --dev-bind / / \
    --bind "$RUN_DIR/drm" /sys/class/drm \
    -- dbus-run-session -- "$TOOLS_DIR/session-inner.sh" \
    <&0 2> "$RUN_DIR/session.log" &
SESSION_PID=$!
SESSION_PGID="$(ps -o pgid= -p "$SESSION_PID" 2>/dev/null | tr -d ' ' || true)"
SESSION_PGID="${SESSION_PGID:-$SESSION_PID}"

SESSION_RC=0
wait "$SESSION_PID" || SESSION_RC=$?
if [ "$SESSION_RC" -ne 0 ]; then
    printf '\033[36m[nested]\033[0m \033[31merror:\033[0m session failed (status %s); last lines of session.log:\n' \
        "$SESSION_RC" >&2
    tail -20 "$RUN_DIR/session.log" | sed 's/^/  /' >&2
    # Keep the tree so the log paths printed above still resolve.
    KEEP=1
    exit "$SESSION_RC"
fi
