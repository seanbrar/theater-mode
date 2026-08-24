#!/usr/bin/env bash
#
# Build and run a disposable Arch Linux + Plasma guest with two virtual displays.
#
# This answers the one question the nested harness cannot: does the current checkout
# install and run cleanly on a current Arch Linux Plasma system. Everything the guest is
# made of -- the base image, the package list, the autologin -- is declared in this
# directory, so the guest can be thrown away and rebuilt rather than maintained.
#
# QEMU is not installed on the host by design. Run this inside the container defined by
# tools/vm/distrobox.ini; see tools/vm/README.md.

set -euo pipefail

die() { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }
note() { printf '\033[35m[vm]\033[0m %s\n' "$*"; }

TOOLS_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd -- "$TOOLS_DIR/../.." && pwd)"

# Images live outside the checkout: they are large, rebuildable, and must never be
# swept into a release tarball or a git status.
DEFAULT_STATE_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/theater-mode/vm"
STATE_DIR="${THEATER_VM_STATE_DIR:-$DEFAULT_STATE_DIR}"
STATE_MARKER="$STATE_DIR/.theater-mode-vm-state"
BASE_IMAGE="$STATE_DIR/arch-base.qcow2"
GOLDEN_IMAGE="$STATE_DIR/golden.qcow2"
GOLDEN_BUILT="$STATE_DIR/golden.built"
BUILD_IMAGE="$STATE_DIR/golden.building.qcow2"
SEED_ISO="$STATE_DIR/seed.iso"
SEED_DIR="$STATE_DIR/seed"
SSH_KEY="$STATE_DIR/id_ed25519"
BASE_CHECKSUM="$STATE_DIR/arch-base.qcow2.sha256"
BASE_SOURCE="$STATE_DIR/arch-base.url"
RUN_IMAGE=""
XVFB_DISPLAY_FILE=""
XVFB_LOG="$STATE_DIR/xvfb.log"

BASE_URL="${THEATER_VM_BASE_URL:-https://geo.mirror.pkgbuild.com/images/latest/Arch-Linux-x86_64-cloudimg.qcow2}"
MEMORY="${THEATER_VM_MEMORY:-4096}"
CPUS="${THEATER_VM_CPUS:-4}"
OUTPUTS="${THEATER_VM_OUTPUTS:-2}"
SSH_PORT="${THEATER_VM_SSH_PORT:-}"
BUILD_TIMEOUT="${THEATER_VM_BUILD_TIMEOUT:-1200}"
CHECK_TIMEOUT="${THEATER_VM_CHECK_TIMEOUT:-300}"
QEMU_PID=""
XVFB_PID=""
usage() {
    cat <<'USAGE'
Usage: tools/vm/vm.sh <command>

  fetch      download and checksum-verify the Arch cloud image
  build      provision the reusable golden image (Plasma, autologin)
  run        boot a graphical throwaway copy with two virtual displays
  console    boot a throwaway copy with a serial console for debugging
  check      boot headless, install the checkout, and exercise the installed effect
  inspect    verify cached images and print their provenance
  clean      remove every generated image

Environment:
  THEATER_VM_STATE_DIR   where images live (default: $XDG_DATA_HOME/theater-mode/vm)
  THEATER_VM_BASE_URL    Arch cloud image URL (cached until clean)
  THEATER_VM_MEMORY      guest RAM in MiB (default: 4096)
  THEATER_VM_CPUS        guest vCPUs (default: 4)
  THEATER_VM_OUTPUTS     virtual displays (default: 2)
  THEATER_VM_SSH_PORT    optional localhost port forwarded to guest SSH
  THEATER_VM_BUILD_TIMEOUT  provisioning deadline in seconds (default: 1200)
  THEATER_VM_CHECK_TIMEOUT  deadline for the guest to accept SSH (default: 300)
USAGE
}

require_qemu() {
    command -v qemu-system-x86_64 >/dev/null \
        || die "qemu-system-x86_64 not found; run this inside the theater-mode-vm container"
    [ -w /dev/kvm ] || die "/dev/kvm is not writable; KVM acceleration is required"
}

require_image_tools() {
    command -v qemu-img >/dev/null \
        || die "qemu-img not found; run this inside the theater-mode-vm container"
}

require_headless() {
    command -v Xvfb >/dev/null \
        || die "Xvfb not found; run this inside the theater-mode-vm container"
}

validate_settings() {
    [[ "$MEMORY" =~ ^[1-9][0-9]*$ ]] || die "THEATER_VM_MEMORY must be whole MiB"
    [[ "$CPUS" =~ ^[1-9][0-9]*$ ]] || die "THEATER_VM_CPUS must be a positive integer"
    [[ "$OUTPUTS" =~ ^[1-9][0-9]*$ ]] || die "THEATER_VM_OUTPUTS must be a positive integer"
    [[ "$BUILD_TIMEOUT" =~ ^[1-9][0-9]*$ ]] \
        || die "THEATER_VM_BUILD_TIMEOUT must be whole seconds"
    [[ "$CHECK_TIMEOUT" =~ ^[1-9][0-9]*$ ]] \
        || die "THEATER_VM_CHECK_TIMEOUT must be whole seconds"
    if [ -n "$SSH_PORT" ]; then
        [[ "$SSH_PORT" =~ ^[0-9]+$ ]] && [ "$SSH_PORT" -ge 1024 ] && [ "$SSH_PORT" -le 65535 ] \
            || die "THEATER_VM_SSH_PORT must be a port from 1024 to 65535"
    fi
}

validate_state_dir() {
    local home_resolved repo_resolved resolved
    resolved="$(realpath -m -- "$STATE_DIR")" || die "could not resolve VM state directory: $STATE_DIR"
    home_resolved="$(realpath -m -- "$HOME")"
    repo_resolved="$(realpath -m -- "$REPO_DIR")"
    case "$resolved" in
        /|/tmp|/var/tmp|"$home_resolved"|"$repo_resolved")
            die "THEATER_VM_STATE_DIR must name a dedicated directory, not $resolved"
            ;;
    esac
    STATE_DIR="$resolved"
    STATE_MARKER="$STATE_DIR/.theater-mode-vm-state"
    BASE_IMAGE="$STATE_DIR/arch-base.qcow2"
    GOLDEN_IMAGE="$STATE_DIR/golden.qcow2"
    GOLDEN_BUILT="$STATE_DIR/golden.built"
    BUILD_IMAGE="$STATE_DIR/golden.building.qcow2"
    SEED_ISO="$STATE_DIR/seed.iso"
    SEED_DIR="$STATE_DIR/seed"
    SSH_KEY="$STATE_DIR/id_ed25519"
    BASE_CHECKSUM="$STATE_DIR/arch-base.qcow2.sha256"
    BASE_SOURCE="$STATE_DIR/arch-base.url"
    XVFB_LOG="$STATE_DIR/xvfb.log"
}

ensure_ssh_key() {
    [ -f "$SSH_KEY" ] && return 0
    note "generating a guest SSH key"
    ssh-keygen -q -t ed25519 -N "" -C theater-mode-vm -f "$SSH_KEY"
}

# cloud-init receives the public half of a key generated on this machine, so `check` can
# reach the guest without a password and the repository carries no private key.
stage_seed() {
    mkdir -p "$SEED_DIR"
    cp "$TOOLS_DIR/cloud-init/meta-data" "$SEED_DIR/meta-data"
    awk -v key="$(cat "$SSH_KEY.pub")" '
        { print }
        /^    plain_text_passwd: tester$/ {
            print "    ssh_authorized_keys:"
            print "      - " key
        }
    ' "$TOOLS_DIR/cloud-init/user-data" > "$SEED_DIR/user-data"
    grep -q ssh_authorized_keys "$SEED_DIR/user-data" \
        || die "could not place the SSH key in the cloud-init user data"
}

prepare_state_dir() {
    validate_state_dir
    if [ -d "$STATE_DIR" ] && [ ! -f "$STATE_MARKER" ] \
        && [ "$STATE_DIR" != "$(realpath -m -- "$DEFAULT_STATE_DIR")" ] \
        && [ -n "$(find "$STATE_DIR" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]; then
        die "refusing to use nonempty state directory without $STATE_MARKER"
    fi
    mkdir -p "$STATE_DIR"
    # qcow2 on btrfs fragments badly under copy-on-write. Marking the directory NOCOW
    # before any image is created keeps the guest's disk I/O from degrading over time.
    # This is a no-op on filesystems that do not support the attribute.
    chattr +C "$STATE_DIR" 2>/dev/null || true
    : > "$STATE_MARKER"
}

cleanup_build() {
    rm -f "$BUILD_IMAGE" "$GOLDEN_BUILT.part"
}

cmd_fetch() {
    prepare_state_dir
    if [ -f "$BASE_IMAGE" ]; then
        [ -f "$BASE_CHECKSUM" ] && [ -f "$BASE_SOURCE" ] \
            || die "cached base image lacks verification metadata; remove it and run fetch again"
        [ "$(cat "$BASE_SOURCE")" = "$BASE_URL" ] \
            || die "cached base came from a different URL; run clean or restore THEATER_VM_BASE_URL"
        verify_base_image "$BASE_IMAGE"
        note "verified cached base image: $BASE_IMAGE"
        return
    fi

    note "downloading $BASE_URL"
    curl -fsL --remove-on-error -o "$BASE_CHECKSUM.part" "$BASE_URL.SHA256" \
        || die "the image checksum could not be downloaded"
    curl -fL --remove-on-error --progress-bar -o "$BASE_IMAGE.part" "$BASE_URL"
    mv "$BASE_CHECKSUM.part" "$BASE_CHECKSUM"
    verify_base_image "$BASE_IMAGE.part"
    mv "$BASE_IMAGE.part" "$BASE_IMAGE"
    printf '%s\n' "$BASE_URL" > "$BASE_SOURCE"
}

verify_base_image() {
    local actual expected
    expected="$(awk 'NR == 1 { print $1 }' "$BASE_CHECKSUM")"
    [[ "$expected" =~ ^[[:xdigit:]]{64}$ ]] || die "published checksum has an invalid format"
    actual="$(sha256sum "$1" | awk '{ print $1 }')"
    [ "$actual" = "${expected,,}" ] || die "checksum mismatch on $1"
}

cmd_build() {
    require_qemu
    require_image_tools
    validate_settings
    cmd_fetch
    trap cleanup_build EXIT
    trap 'exit 130' INT TERM

    ensure_ssh_key
    note "building cloud-init seed"
    command -v xorriso >/dev/null || die "xorriso is required to build the seed image"
    stage_seed
    xorriso -as mkisofs -quiet -output "$SEED_ISO" -volid cidata -joliet -rock \
        "$SEED_DIR/user-data" "$SEED_DIR/meta-data"

    note "creating candidate image from base"
    rm -f "$BUILD_IMAGE"
    qemu-img create -f qcow2 -F qcow2 -b "$BASE_IMAGE" "$BUILD_IMAGE" 20G >/dev/null

    note "first boot: installing Plasma. This is the slow step, and it happens once."
    timeout --foreground --signal=TERM "$BUILD_TIMEOUT" qemu-system-x86_64 \
        -machine q35,accel=kvm -cpu host -smp "$CPUS" -m "$MEMORY" \
        -drive file="$BUILD_IMAGE",if=virtio,format=qcow2 \
        -drive file="$SEED_ISO",if=virtio,format=raw,readonly=on \
        -netdev user,id=net0 -device virtio-net-pci,netdev=net0 \
        -nographic -no-reboot

    qemu-img check -q "$BUILD_IMAGE" || die "provisioned image failed qemu-img validation"
    date -u +'%Y-%m-%dT%H:%M:%SZ' > "$GOLDEN_BUILT.part"
    mv -f "$BUILD_IMAGE" "$GOLDEN_IMAGE"
    mv -f "$GOLDEN_BUILT.part" "$GOLDEN_BUILT"
    trap - EXIT INT TERM
    note "golden image ready: $GOLDEN_IMAGE"
}

make_run_image() {
    local run_image
    run_image="$(mktemp --tmpdir="$STATE_DIR" theater-run-XXXXXX.qcow2)"
    rm -f "$run_image"
    qemu-img create -q -f qcow2 -F qcow2 -b "$GOLDEN_IMAGE" "$run_image"
    printf '%s\n' "$run_image"
}

remove_run_image() {
    [ -z "$RUN_IMAGE" ] || rm -f "$RUN_IMAGE"
}

remove_xvfb_display_file() {
    [ -z "$XVFB_DISPLAY_FILE" ] || rm -f "$XVFB_DISPLAY_FILE"
}

cmd_run() {
    local netdev="user,id=net0"
    require_qemu
    require_image_tools
    validate_settings
    prepare_state_dir
    [ -f "$GOLDEN_IMAGE" ] || die "no golden image; run: tools/vm/vm.sh build"
    RUN_IMAGE="$(make_run_image)"
    trap remove_run_image EXIT
    trap 'exit 130' INT TERM

    note "booting ephemeral overlay with $OUTPUTS virtual displays"
    note "inside the guest, run: theater-vm-check"
    if [ -n "$SSH_PORT" ]; then
        netdev+=",hostfwd=tcp:127.0.0.1:${SSH_PORT}-:22"
        note "guest SSH: ssh -p $SSH_PORT tester@127.0.0.1 (password: tester)"
    fi

    # Each run writes to its own overlay, leaving the golden image unchanged. virtio-gpu
    # gives the guest real DRM connectors with generated EDID, so display/drm.py works
    # here unmodified.
    qemu-system-x86_64 \
        -machine q35,accel=kvm -cpu host -smp "$CPUS" -m "$MEMORY" \
        -drive file="$RUN_IMAGE",if=virtio,format=qcow2 \
        -device virtio-vga,max_outputs="$OUTPUTS" \
        -display gtk,show-tabs=on \
        -device virtio-tablet-pci -device virtio-keyboard-pci \
        -netdev "$netdev" -device virtio-net-pci,netdev=net0 \
        -virtfs local,path="$REPO_DIR",mount_tag=theater,security_model=mapped-xattr,readonly=on
}

cmd_console() {
    local netdev="user,id=net0"
    require_qemu
    require_image_tools
    validate_settings
    prepare_state_dir
    [ -f "$GOLDEN_IMAGE" ] || die "no golden image; run: tools/vm/vm.sh build"
    RUN_IMAGE="$(make_run_image)"
    trap remove_run_image EXIT
    trap 'exit 130' INT TERM
    note "booting ephemeral overlay in console mode (Ctrl-A X to exit)"
    if [ -n "$SSH_PORT" ]; then
        netdev+=",hostfwd=tcp:127.0.0.1:${SSH_PORT}-:22"
        note "guest SSH: ssh -p $SSH_PORT tester@127.0.0.1 (password: tester)"
    fi
    qemu-system-x86_64 \
        -machine q35,accel=kvm -cpu host -smp "$CPUS" -m "$MEMORY" \
        -drive file="$RUN_IMAGE",if=virtio,format=qcow2 \
        -netdev "$netdev" -device virtio-net-pci,netdev=net0 \
        -virtfs local,path="$REPO_DIR",mount_tag=theater,security_model=mapped-xattr,readonly=on \
        -nographic
}

free_port() {
    local port
    for _ in $(seq 20); do
        port=$(( 20000 + RANDOM % 20000 ))
        timeout 1 bash -c "exec 3<>/dev/tcp/127.0.0.1/$port" 2>/dev/null && continue
        printf '%s\n' "$port"
        return 0
    done
    die "could not find a free local port for the guest SSH forward"
}

guest_ssh() {
    local port=$1
    shift
    ssh -i "$SSH_KEY" -p "$port" \
        -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
        -o LogLevel=ERROR -o ConnectTimeout=5 -o BatchMode=yes -o IdentitiesOnly=yes \
        tester@127.0.0.1 "$@"
}

guest_ssh_bounded() {
    local limit=$1
    shift
    timeout --foreground "$limit" ssh -i "$SSH_KEY" -p "$1" \
        -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
        -o LogLevel=ERROR -o ConnectTimeout=5 -o BatchMode=yes -o IdentitiesOnly=yes \
        tester@127.0.0.1 "${@:2}"
}

# The guest is reachable well before Plasma has published its session environment, so
# waiting for SSH alone would run the check against a half-started desktop. The desktop
# user's systemd manager reporting a running graphical session is the later signal.
wait_for_guest() {
    local port=$1 deadline remaining
    deadline=$((SECONDS + CHECK_TIMEOUT))
    while [ "$SECONDS" -lt "$deadline" ]; do
        process_running "$QEMU_PID" || die "the guest exited before it became reachable"
        remaining=$((deadline - SECONDS))
        if guest_ssh_bounded "$remaining" "$port" \
            "systemctl --user is-active graphical-session.target" \
            >/dev/null 2>&1; then
            return 0
        fi
        [ "$SECONDS" -lt "$deadline" ] || break
        sleep 2
    done
    die "the guest did not present a graphical session within ${CHECK_TIMEOUT}s"
}

# A virtio-gpu connector reports connected only when a display backend attaches a scanout
# to it. Running QEMU against Xvfb with GTK tabs attaches a virtual console to each
# requested head so all connectors report connected without a physical screen.
assert_guest_displays() {
    local port=$1 expected=$2 connected
    connected="$(guest_ssh "$port" bash -s -- "$expected" <<'GUEST'
set -euo pipefail

expected=$1
export XDG_RUNTIME_DIR="/run/user/$(id -u)"
export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"
published="$(systemctl --user show-environment)"
WAYLAND_DISPLAY="$(printf '%s\n' "$published" | sed -n 's/^WAYLAND_DISPLAY=//p')"
export WAYLAND_DISPLAY
[ -n "$WAYLAND_DISPLAY" ] || { echo "Plasma did not publish WAYLAND_DISPLAY" >&2; exit 1; }

drm_names="$(
    for status in /sys/class/drm/card*-*/status; do
        [ -f "$status" ] || continue
        [ "$(cat "$status")" = connected ] || continue
        connector="${status%/status}"
        basename "$connector" | sed 's/^card[0-9]*-//'
    done | LC_ALL=C sort
)"
kscreen_json="$(kscreen-doctor --json)"

DRM_NAMES="$drm_names" KSCREEN_JSON="$kscreen_json" python3 - "$expected" <<'PY'
import json
import os
import sys

expected = int(sys.argv[1])
drm = sorted(name for name in os.environ["DRM_NAMES"].splitlines() if name)
configuration = json.loads(os.environ["KSCREEN_JSON"])
plasma = sorted(
    output["name"]
    for output in configuration["outputs"]
    if output.get("connected") and output.get("enabled")
)

if len(drm) != expected:
    raise SystemExit(f"DRM reports {len(drm)} connected displays, expected {expected}: {drm}")
if plasma != drm:
    raise SystemExit(f"Plasma outputs {plasma} do not match connected DRM outputs {drm}")
print(len(drm))
PY
GUEST
)" || die "the guest display topology is not usable"
    if [ "$connected" -eq 1 ]; then
        note "guest DRM and Plasma agree on 1 connected display"
    else
        note "guest DRM and Plasma agree on $connected connected displays"
    fi
}

assert_guest_effect() {
    local port=$1 expected=$2
    guest_ssh "$port" bash -s -- "$expected" <<'GUEST'
set -euo pipefail

expected=$1
client="$HOME/.local/bin/theater-mode"
export XDG_RUNTIME_DIR="/run/user/$(id -u)"
export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"
published="$(systemctl --user show-environment)"
for key in XDG_SESSION_TYPE XDG_CURRENT_DESKTOP WAYLAND_DISPLAY DISPLAY; do
    value="$(printf '%s\n' "$published" | sed -n "s/^$key=//p")"
    [ -z "$value" ] || export "$key=$value"
done

output="$("$client" status --json | python3 -c '
import json
import sys

outputs = json.load(sys.stdin)["outputs"]
if not outputs:
    raise SystemExit("daemon reports no outputs")
print(outputs[0])
')"
"$client" simulate 0 "$output" >/dev/null
trap '"$client" clear >/dev/null 2>&1 || true' EXIT

activated=0
for _ in $(seq 40); do
    status="$("$client" status --json)"
    if STATUS="$status" python3 - "$expected" "$output" <<'PY'
import json
import os
import sys

expected = int(sys.argv[1])
active = sys.argv[2]
status = json.loads(os.environ["STATUS"])
others = sorted(output for output in status["outputs"] if output != active)
valid = (
    len(status["outputs"]) == expected
    and status["active_output"] == active
    and sorted(status["affected_outputs"]) == others
)
if expected > 1:
    valid = valid and status["effect_process_running"]
raise SystemExit(0 if valid else 1)
PY
    then
        activated=1
        break
    fi
    sleep 0.25
done
[ "$activated" -eq 1 ] || { echo "installed effect did not activate" >&2; exit 1; }

"$client" clear >/dev/null
restored=0
for _ in $(seq 40); do
    status="$("$client" status --json)"
    if STATUS="$status" python3 - "$expected" <<'PY'
import json
import os
import sys

expected = int(sys.argv[1])
status = json.loads(os.environ["STATUS"])
valid = (
    len(status["outputs"]) == expected
    and status["active_output"] is None
    and not status["affected_outputs"]
)
raise SystemExit(0 if valid else 1)
PY
    then
        restored=1
        break
    fi
    sleep 0.25
done
[ "$restored" -eq 1 ] || { echo "installed effect did not restore displays" >&2; exit 1; }
trap - EXIT
GUEST
}

process_running() {
    local pid=$1 state
    kill -0 "$pid" 2>/dev/null || return 1
    if [ -f "/proc/$pid/status" ]; then
        if grep -q '^State:[[:space:]]*Z' "/proc/$pid/status" 2>/dev/null; then
            return 1
        fi
        return 0
    fi
    state="$(ps -o stat= -p "$pid" 2>/dev/null || true)"
    [[ "$state" != Z* ]]
}

stop_guest() {
    local waited=0

    if [ -n "$QEMU_PID" ] && kill -0 "$QEMU_PID" 2>/dev/null; then
        kill "$QEMU_PID" 2>/dev/null || true
    fi
    while [ -n "$QEMU_PID" ] && process_running "$QEMU_PID" \
        && [ "$waited" -lt 30 ]; do
        sleep 1
        waited=$(( waited + 1 ))
    done
    if [ -n "$QEMU_PID" ] && kill -0 "$QEMU_PID" 2>/dev/null; then
        kill -9 "$QEMU_PID" 2>/dev/null || true
    fi

    [ -z "$QEMU_PID" ] || wait "$QEMU_PID" 2>/dev/null || true

    if [ -n "$XVFB_PID" ] && kill -0 "$XVFB_PID" 2>/dev/null; then
        kill "$XVFB_PID" 2>/dev/null || true
    fi
    waited=0
    while [ -n "$XVFB_PID" ] && process_running "$XVFB_PID" \
        && [ "$waited" -lt 5 ]; do
        sleep 1
        waited=$(( waited + 1 ))
    done
    if [ -n "$XVFB_PID" ] && kill -0 "$XVFB_PID" 2>/dev/null; then
        kill -9 "$XVFB_PID" 2>/dev/null || true
    fi
    [ -z "$XVFB_PID" ] || wait "$XVFB_PID" 2>/dev/null || true
}

check_cleanup() {
    stop_guest
    remove_run_image
    remove_xvfb_display_file
}

cmd_check() {
    local display_number port status
    require_qemu
    require_image_tools
    require_headless
    validate_settings
    prepare_state_dir
    [ -f "$GOLDEN_IMAGE" ] || die "no golden image; run: tools/vm/vm.sh build"
    [ -f "$SSH_KEY" ] || die "no guest SSH key; run: tools/vm/vm.sh build"
    port="${SSH_PORT:-$(free_port)}"
    RUN_IMAGE="$(make_run_image)"
    trap check_cleanup EXIT
    trap 'exit 130' INT TERM

    note "booting the guest headless with $OUTPUTS virtual displays on port $port"
    XVFB_DISPLAY_FILE="$(mktemp --tmpdir="$STATE_DIR" theater-xvfb-XXXXXX.display)"
    # The server has no network listener. Disabling access control avoids creating a
    # throwaway cookie solely for the local QEMU process.
    Xvfb -displayfd 3 -screen 0 640x480x24 -nolisten tcp -ac \
        3> "$XVFB_DISPLAY_FILE" > "$XVFB_LOG" 2>&1 &
    XVFB_PID=$!
    for _ in $(seq 40); do
        if [ -s "$XVFB_DISPLAY_FILE" ]; then
            display_number="$(cat "$XVFB_DISPLAY_FILE")"
            break
        fi
        if ! process_running "$XVFB_PID"; then
            tail -20 "$XVFB_LOG" >&2
            die "Xvfb exited before publishing a display"
        fi
        sleep 0.25
    done
    if ! [[ "${display_number:-}" =~ ^[0-9]+$ ]]; then
        tail -20 "$XVFB_LOG" >&2
        die "Xvfb did not publish a display number"
    fi

    DISPLAY=":$display_number" qemu-system-x86_64 \
        -machine q35,accel=kvm -cpu host -smp "$CPUS" -m "$MEMORY" \
        -drive file="$RUN_IMAGE",if=virtio,format=qcow2 \
        -device virtio-vga,max_outputs="$OUTPUTS" \
        -display gtk,show-tabs=on \
        -netdev "user,id=net0,hostfwd=tcp:127.0.0.1:${port}-:22" \
        -device virtio-net-pci,netdev=net0 \
        -virtfs local,path="$REPO_DIR",mount_tag=theater,security_model=mapped-xattr,readonly=on \
        &
    QEMU_PID=$!

    wait_for_guest "$port"
    assert_guest_displays "$port" "$OUTPUTS"
    note "installing the checkout in the guest and running doctor"
    set +e
    guest_ssh "$port" theater-vm-check
    status=$?
    if [ "$status" -eq 0 ]; then
        note "activating and clearing the installed effect"
        assert_guest_effect "$port" "$OUTPUTS"
        status=$?
    fi
    set -e

    if [ "$status" -eq 0 ]; then
        note "guest reports a healthy installation"
    else
        note "guest check failed with status $status"
    fi
    return "$status"
}

cmd_inspect() {
    validate_state_dir
    require_image_tools
    [ -f "$BASE_IMAGE" ] || die "no base image; run: tools/vm/vm.sh fetch"
    [ -f "$BASE_CHECKSUM" ] && [ -f "$BASE_SOURCE" ] \
        || die "cached base image lacks verification metadata"
    verify_base_image "$BASE_IMAGE"
    note "base source: $(cat "$BASE_SOURCE")"
    note "base sha256: $(awk 'NR == 1 { print $1 }' "$BASE_CHECKSUM")"
    qemu-img info --backing-chain "$BASE_IMAGE"
    if [ -f "$GOLDEN_IMAGE" ]; then
        if [ -f "$GOLDEN_BUILT" ]; then
            note "golden built: $(cat "$GOLDEN_BUILT")"
        else
            note "golden build time is unknown; rebuild to record it"
        fi
        qemu-img check -q "$GOLDEN_IMAGE" || die "golden image failed qemu-img validation"
        qemu-img info --backing-chain "$GOLDEN_IMAGE"
    else
        note "golden image has not been built"
    fi
    report_stale_overlays
}

# A run overlay is deleted when its qemu-system-x86_64 exits. One that outlives its run
# means qemu was killed without its launcher, so nothing is going to reclaim the space.
report_stale_overlays() {
    local overlays
    overlays="$(find "$STATE_DIR" -maxdepth 1 -name 'theater-run-*.qcow2' -print 2>/dev/null)"
    [ -n "$overlays" ] || return 0
    note "stale run overlays are present; remove them with: tools/vm/vm.sh clean"
    printf '%s\n' "$overlays" | sed 's/^/    /'
}

cmd_clean() {
    validate_state_dir
    if [ ! -e "$STATE_DIR" ]; then
        note "no VM state directory exists at $STATE_DIR"
        return 0
    fi
    if [ ! -f "$STATE_MARKER" ] && [ "$STATE_DIR" != "$(realpath -m -- "$DEFAULT_STATE_DIR")" ]; then
        if [ -n "$(find "$STATE_DIR" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]; then
            die "refusing to clean an unrecognized state directory without $STATE_MARKER"
        fi
        note "no generated VM state exists at $STATE_DIR"
        return 0
    fi
    note "removing generated images under $STATE_DIR"
    rm -f "$GOLDEN_IMAGE" "$GOLDEN_BUILT" "$GOLDEN_BUILT.part" "$BUILD_IMAGE" \
        "$SEED_ISO" "$BASE_IMAGE" \
        "$BASE_CHECKSUM" "$BASE_CHECKSUM.part" "$BASE_SOURCE" "$BASE_IMAGE.part" \
        "$XVFB_LOG"
    find "$STATE_DIR" -maxdepth 1 -name 'theater-run-*.qcow2' -delete 2>/dev/null || true
    find "$STATE_DIR" -maxdepth 1 -name 'theater-xvfb-*.display' -delete 2>/dev/null || true
    rm -rf "$SEED_DIR"
    rm -f "$SSH_KEY" "$SSH_KEY.pub"
    rm -f "$STATE_MARKER"
}

[ $# -le 1 ] || die "expected one command (try --help)"
case "${1:-}" in
    fetch) cmd_fetch ;;
    build) cmd_build ;;
    run) cmd_run ;;
    console) cmd_console ;;
    check) cmd_check ;;
    inspect) cmd_inspect ;;
    clean) cmd_clean ;;
    -h|--help|"") usage ;;
    *) die "unknown command: $1 (try --help)" ;;
esac
