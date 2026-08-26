#!/usr/bin/env bash
#
# Boot Valve's own SteamOS images in a disposable virtual machine.
#
# vm.sh answers whether the checkout works on a current upstream Arch Plasma system.
# This answers a different question: whether it works on the Plasma that SteamOS ships,
# with the read-only root, the A/B partition sets, and the package versions that come
# with it.
#
# Every base image comes from Valve's image server. VM-only changes are applied to a
# qcow2 overlay immediately before boot, leaving the downloaded or installed drive
# unchanged and making ordinary launches reproducible.
#
# Gaming Mode does not run under QEMU: gamescope needs a GPU the emulated hardware does
# not provide. SteamOS 3.7 and later detect the virtual machine and boot to Desktop Mode
# instead, which is the mode this project targets anyway.
#
# QEMU is not installed on the host by design. Run this inside the container defined by
# tools/vm/distrobox.ini; see tools/vm/README.md.

set -euo pipefail

die() { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }
note() { printf '\033[35m[steamos]\033[0m %s\n' "$*"; }

TOOLS_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd -- "$TOOLS_DIR/../.." && pwd)"

# Sits beside the Arch guest's images under the same cache root, in its own directory so
# that either guest can be discarded without disturbing the other.
DEFAULT_STATE_ROOT="${XDG_DATA_HOME:-$HOME/.local/share}/theater-mode/vm"
STATE_ROOT="${THEATER_VM_STATE_DIR:-$DEFAULT_STATE_ROOT}"
STATE_DIR="$STATE_ROOT/steamos"
STATE_MARKER="$STATE_DIR/.theater-mode-steamos-state"
DISK="$STATE_DIR/steamos.qcow2"
DISK_ORIGIN="$STATE_DIR/steamos.origin"
PERSIST_IMAGE="$STATE_DIR/steamos-persistent.qcow2"
NVRAM="$STATE_DIR/OVMF_VARS.fd"
INSTALLER="$STATE_DIR/installer.img"
INSTALLER_OVERLAY="$STATE_DIR/installer-overlay.qcow2"
INSTALLER_ORIGIN="$STATE_DIR/installer.origin"
SSH_KEY="$STATE_ROOT/id_ed25519"
QMP_SOCKET="$STATE_DIR/qmp.sock"
RUN_IMAGE=""
RUN_NVRAM=""
QEMU_PID=""
DISPLAY_ARGS=()

IMAGE_HOST="${THEATER_STEAMOS_HOST:-https://steamdeck-images.steamos.cloud}"
VARIANT="${THEATER_STEAMOS_VARIANT:-steamdeck}"
BUILD="${THEATER_STEAMOS_BUILD:-stable}"
DISK_SIZE="${THEATER_STEAMOS_DISK:-64G}"
MEMORY="${THEATER_STEAMOS_MEMORY:-8192}"
CPUS="${THEATER_STEAMOS_CPUS:-4}"
OUTPUTS="${THEATER_STEAMOS_OUTPUTS:-2}"
SSH_PORT="${THEATER_STEAMOS_SSH_PORT:-}"
SETTLE="${THEATER_STEAMOS_SETTLE:-30}"
CHECK_TIMEOUT="${THEATER_STEAMOS_CHECK_TIMEOUT:-300}"
PERSIST="${THEATER_STEAMOS_PERSIST:-0}"
SCAN_LIMIT="${THEATER_STEAMOS_SCAN_LIMIT:-80}"
MACHINE="${THEATER_STEAMOS_MACHINE:-pc}"

usage() {
    cat <<'USAGE'
Usage: tools/vm/steamos.sh <command> [argument]

  builds [N]         list the newest N builds Valve publishes, with branch and version
  fetch              download the newest official repair image
  install            install SteamOS onto a fresh virtual drive using that image
  import [BUILD]     write a published build straight to the drive, without the installer
  run                boot the guest with virtual displays and the checkout attached
  check              install from the checkout, test displays, and verify doctor
  ssh [COMMAND...]   connect to the running guest or run a command over SSH
  provision          prepare the persistent overlay used by persistent runs
  console            boot the guest with a serial console (Ctrl-A X to exit)
  screenshot [FILE]  boot headless and capture the framebuffer once the desktop settles
  inspect            print what is cached and which image it came from
  clean              remove the drive, the installer, and every overlay

BUILD selects which published image `import` writes. It accepts a branch name
(stable, beta, main, bc, pc, staging), a version such as 3.8.16, or an exact build
id such as 20260716.1. The default is stable.

Environment:
  THEATER_STEAMOS_VARIANT     steamdeck (default) or fremont, Valve's generic-PC variant
  THEATER_STEAMOS_BUILD       default BUILD for import (default: stable)
  THEATER_STEAMOS_DISK        virtual drive size (default: 64G)
  THEATER_STEAMOS_OUTPUTS     number of virtual displays (default: 2)
  THEATER_STEAMOS_MEMORY      guest RAM in MiB (default: 8192)
  THEATER_STEAMOS_CPUS        virtual CPU cores (default: 4)
  THEATER_STEAMOS_SSH_PORT    localhost port forwarded to guest SSH (default: 2222)
  THEATER_STEAMOS_SETTLE      seconds to wait before `screenshot` captures (default: 30)
  THEATER_STEAMOS_PERSIST     1 to retain guest changes in a persistent overlay
  THEATER_STEAMOS_HOST        image server base URL
  THEATER_STEAMOS_MACHINE     QEMU machine type: pc (default) or q35
  THEATER_VM_STATE_DIR        cache root, shared with vm.sh
USAGE
}

require_qemu() {
    command -v qemu-system-x86_64 >/dev/null \
        || die "qemu-system-x86_64 is missing; run this inside the theater-mode-vm container"
    [ -w /dev/kvm ] || die "/dev/kvm is not writable; KVM is required"
}

require_image_tools() {
    for tool in qemu-img curl python3; do
        command -v "$tool" >/dev/null || die "$tool is required"
    done
}

validate_settings() {
    [[ "$MEMORY" =~ ^[0-9]+$ && "$MEMORY" -ge 2048 ]] \
        || die "THEATER_STEAMOS_MEMORY must be at least 2048"
    [[ "$CPUS" =~ ^[0-9]+$ && "$CPUS" -ge 1 ]] \
        || die "THEATER_STEAMOS_CPUS must be a positive integer"
    [[ "$OUTPUTS" =~ ^[0-9]+$ && "$OUTPUTS" -ge 1 && "$OUTPUTS" -le 16 ]] \
        || die "THEATER_STEAMOS_OUTPUTS must be between 1 and 16"
    [[ "$DISK_SIZE" =~ ^[0-9]+[GT]$ ]] || die "THEATER_STEAMOS_DISK must look like 64G"
    [[ "$VARIANT" =~ ^[a-z]+$ ]] || die "THEATER_STEAMOS_VARIANT must be a bare name"
    [[ "$CHECK_TIMEOUT" =~ ^[1-9][0-9]*$ ]] \
        || die "THEATER_STEAMOS_CHECK_TIMEOUT must be whole seconds"
    [[ "$SETTLE" =~ ^[0-9]+$ ]] \
        || die "THEATER_STEAMOS_SETTLE must be whole seconds"
    [[ "$SCAN_LIMIT" =~ ^[1-9][0-9]*$ ]] \
        || die "THEATER_STEAMOS_SCAN_LIMIT must be a positive integer"
    case "$PERSIST" in
        0|1) ;;
        *) die "THEATER_STEAMOS_PERSIST must be 0 or 1" ;;
    esac
    case "$MACHINE" in
        pc|q35) ;;
        *) die "THEATER_STEAMOS_MACHINE must be pc or q35" ;;
    esac
    if [ -n "$SSH_PORT" ]; then
        [[ "$SSH_PORT" =~ ^[0-9]+$ && "$SSH_PORT" -ge 1024 && "$SSH_PORT" -le 65535 ]] \
            || die "THEATER_STEAMOS_SSH_PORT must be between 1024 and 65535"
    fi
}

validate_state_dir() {
    local home_resolved repo_resolved resolved
    resolved="$(realpath -m -- "$STATE_ROOT")" || die "could not resolve VM state directory: $STATE_ROOT"
    home_resolved="$(realpath -m -- "$HOME")"
    repo_resolved="$(realpath -m -- "$REPO_DIR")"
    case "$resolved" in
        /|/tmp|/var/tmp|"$home_resolved"|"$repo_resolved")
            die "THEATER_VM_STATE_DIR must name a dedicated directory, not $resolved"
            ;;
    esac
    STATE_ROOT="$resolved"
    STATE_DIR="$STATE_ROOT/steamos"
    STATE_MARKER="$STATE_DIR/.theater-mode-steamos-state"
    DISK="$STATE_DIR/steamos.qcow2"
    DISK_ORIGIN="$STATE_DIR/steamos.origin"
    PERSIST_IMAGE="$STATE_DIR/steamos-persistent.qcow2"
    NVRAM="$STATE_DIR/OVMF_VARS.fd"
    INSTALLER="$STATE_DIR/installer.img"
    INSTALLER_OVERLAY="$STATE_DIR/installer-overlay.qcow2"
    INSTALLER_ORIGIN="$STATE_DIR/installer.origin"
    SSH_KEY="$STATE_ROOT/id_ed25519"
    QMP_SOCKET="$STATE_DIR/qmp.sock"
}

prepare_state_dir() {
    validate_state_dir
    if [ -d "$STATE_DIR" ] && [ ! -f "$STATE_MARKER" ] \
        && [ "$STATE_DIR" != "$(realpath -m -- "$DEFAULT_STATE_ROOT/steamos")" ] \
        && [ -n "$(find "$STATE_DIR" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]; then
        die "refusing to use nonempty state directory without $STATE_MARKER"
    fi
    mkdir -p "$STATE_DIR"
    # Disk images are the worst possible case for copy-on-write, and the cache root is on
    # Btrfs on the systems this project is developed on.
    chattr +C "$STATE_DIR" 2>/dev/null || true
    : > "$STATE_MARKER"
}

ensure_ssh_key() {
    [ -f "$SSH_KEY" ] && return 0
    note "generating a guest SSH key"
    ssh-keygen -q -t ed25519 -N "" -C theater-mode-steamos -f "$SSH_KEY"
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

guest_ssh() {
    local port=$1
    shift
    ssh -i "$SSH_KEY" -p "$port" \
        -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
        -o LogLevel=ERROR -o ConnectTimeout=5 -o BatchMode=yes -o IdentitiesOnly=yes \
        deck@127.0.0.1 "$@"
}

guest_ssh_bounded() {
    local limit=$1
    shift
    timeout --foreground "$limit" ssh -i "$SSH_KEY" -p "$1" \
        -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
        -o LogLevel=ERROR -o ConnectTimeout=5 -o BatchMode=yes -o IdentitiesOnly=yes \
        deck@127.0.0.1 "${@:2}"
}

ovmf_code() {
    local candidate
    for candidate in \
        /usr/share/edk2/x64/OVMF_CODE.4m.fd \
        /usr/share/edk2-ovmf/x64/OVMF_CODE.fd \
        /usr/share/OVMF/OVMF_CODE.fd \
        /usr/share/ovmf/x64/OVMF_CODE.fd
    do
        [ -f "$candidate" ] && { printf '%s\n' "$candidate"; return; }
    done
    die "no OVMF firmware found; SteamOS boots over UEFI and cannot start without it"
}

ovmf_vars_template() {
    local code template
    code="$(ovmf_code)"
    template="${code/OVMF_CODE/OVMF_VARS}"
    [ -f "$template" ] || die "found $code but no matching OVMF_VARS image beside it"
    printf '%s\n' "$template"
}

ensure_nvram() {
    [ -f "$NVRAM" ] && return
    # SteamOS installs a boot entry into the UEFI variables, so they outlive the install
    # and belong to the drive rather than to a single boot.
    install -m 0600 "$(ovmf_vars_template)" "$NVRAM"
}

list_dir() {
    curl -fsS --max-time 60 "$IMAGE_HOST/$1/" \
        | grep -oE 'href="[^"]+"' | sed 's/^href="//; s/"$//' | grep -v '^[?.]'
}

json_field() {
    printf '%s' "$2" | tr -d ' \n' \
        | sed -n 's/.*"'"$1"'":"\([^"]*\)".*/\1/p'
}

manifest_for() {
    local build="$1" name
    name="$(list_dir "$VARIANT/$build" | grep -E 'manifest\.json$' | head -1)" || return 1
    [ -n "$name" ] || return 1
    curl -fsS --max-time 30 "$IMAGE_HOST/$VARIANT/$build/$name"
}

build_ids() {
    list_dir "$VARIANT" | grep -E '^[0-9]{8}\.[0-9]+/$' | tr -d / | sort -rV
}

resolve_build() {
    local want="$1" build manifest branch version scanned=0
    if [[ "$want" =~ ^[0-9]{8}\.[0-9]+$ ]]; then
        manifest="$(manifest_for "$want")" || die "no such build: $VARIANT/$want"
        printf '%s %s\n' "$want" "$(json_field version "$manifest")"
        return
    fi
    while read -r build; do
        scanned=$((scanned + 1))
        [ "$scanned" -le "$SCAN_LIMIT" ] || break
        manifest="$(manifest_for "$build")" || continue
        branch="$(json_field branch "$manifest")"
        version="$(json_field version "$manifest")"
        if [ "$want" = "$branch" ] || [ "$want" = "$version" ]; then
            printf '%s %s\n' "$build" "$version"
            return
        fi
    done < <(build_ids)
    die "no build matching '$want' in the newest $SCAN_LIMIT $VARIANT builds"
}

resolve_installer() {
    local name
    name="$(list_dir recovery \
        | grep -E -- '-repair-[0-9]{8}\.[0-9]+-[0-9.]+\.img\.zip$' \
        | sed -E 's/^.*-repair-([0-9]{8}\.[0-9]+)-.*$/\1 &/' \
        | sort -k1,1V | tail -1 | cut -d' ' -f2)"
    [ -n "$name" ] || die "no repair image found in $IMAGE_HOST/recovery/"
    printf '%s\n' "$name"
}

record_origin() {
    printf 'url    %s\nsha256 %s\nfetched %s\n' \
        "$1" "$2" "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" > "$3"
}

cmd_builds() {
    local limit="${1:-15}" build manifest scanned=0
    [[ "$limit" =~ ^[0-9]+$ ]] || die "builds takes a count"
    require_image_tools
    printf '%-16s %-10s %s\n' 'BUILD ID' 'BRANCH' 'VERSION'
    while read -r build; do
        scanned=$((scanned + 1))
        [ "$scanned" -le "$limit" ] || break
        manifest="$(manifest_for "$build")" || continue
        printf '%-16s %-10s %s\n' \
            "$build" "$(json_field branch "$manifest")" "$(json_field version "$manifest")"
    done < <(build_ids)
}

cmd_fetch() {
    local name url archive digest published have
    require_image_tools
    command -v bsdtar >/dev/null || die "bsdtar is required to unpack the repair image"
    prepare_state_dir
    name="$(resolve_installer)"
    url="$IMAGE_HOST/recovery/$name"
    if [ -f "$INSTALLER" ] && [ -f "$INSTALLER_ORIGIN" ] \
        && grep -qxF "url    $url" "$INSTALLER_ORIGIN"; then
        note "already have the newest repair image: $name"
        return
    fi
    archive="$STATE_DIR/$name.part"
    published="$(curl -fsSIL --max-time 60 "$url" \
        | sed -n 's/^[Cc]ontent-[Ll]ength:[[:space:]]*\([0-9]*\).*/\1/p' | tail -1)"
    [ -n "$published" ] || die "the image server did not report a size for $name"
    have="$(stat -c %s "$archive" 2>/dev/null || printf 0)"
    if [ "$have" -gt "$published" ]; then
        rm -f "$archive"
        have=0
    fi
    if [ "$have" -lt "$published" ]; then
        note "downloading $url"
        curl -fL --retry 3 --continue-at - --progress-bar -o "$archive" "$url"
    else
        note "resuming from a complete download of $name"
    fi
    digest="$(sha256sum "$archive" | awk '{ print $1 }')"
    note "unpacking $name"
    rm -f "$INSTALLER.part"
    bsdtar -xOf "$archive" > "$INSTALLER.part"
    mv -f "$INSTALLER.part" "$INSTALLER"
    record_origin "$url" "$digest" "$INSTALLER_ORIGIN"
    rm -f "$archive"
    note "repair image ready: $INSTALLER"
}

new_disk() {
    [ ! -f "$DISK" ] || die "a drive already exists; run: tools/vm/steamos.sh clean"
    rm -f "$NVRAM"
    ensure_nvram
}

# SteamOS defaults to gamescope-wayland, which requires a physical GPU and crash-loops
# under QEMU. Direct-booting a tiny kernel configures SDDM to start Plasma, provides
# passwordless sudo for deck, enables sshd, and installs the host public key in under a second.
provision_disk() (
    local target="$1" initramfs tmp_dir pubkey provision_log status
    ensure_ssh_key
    require_qemu
    [ -f /boot/vmlinuz-linux ] || die "/boot/vmlinuz-linux is missing; install the linux package in the VM container"
    command -v busybox >/dev/null || die "busybox is missing; install busybox in the VM container"
    command -v cpio >/dev/null || die "cpio is missing; install cpio in the VM container"

    note "provisioning SteamOS guest (Plasma autologin, sudoers, sshd)..."
    pubkey="$(cat "$SSH_KEY.pub")"
    tmp_dir="$(mktemp -d)"
    initramfs="$(mktemp --tmpdir="$STATE_DIR" provision-XXXXXX.cpio)"
    provision_log="$STATE_DIR/provision.log"
    # This function's body is a subshell, so the trap fires when it returns rather than
    # when the script exits.
    trap 'rm -rf "$tmp_dir" "$initramfs" "$provision_log"' EXIT

    mkdir -p "$tmp_dir/bin" "$tmp_dir/proc" "$tmp_dir/sys" "$tmp_dir/dev" "$tmp_dir/mnt" \
             "$tmp_dir/lib64" "$tmp_dir/usr/lib"
    ln -s usr/lib "$tmp_dir/lib"
    cp /usr/bin/busybox "$tmp_dir/bin/busybox"
    chmod 755 "$tmp_dir/bin/busybox"
    (cd "$tmp_dir/bin" && for t in sh ash ls cat mkdir mount umount dmesg grep sed awk sleep reboot poweroff chmod cp mv rm blkid find; do
        ln -s busybox "$t"
    done)

    if [ -x /usr/bin/btrfs ]; then
        cp /usr/bin/btrfs "$tmp_dir/bin/btrfs"
        chmod 755 "$tmp_dir/bin/btrfs"
        for lib in $(ldd /usr/bin/btrfs | grep -o "/usr/lib[^ ]*"); do
            cp -L "$lib" "$tmp_dir/usr/lib/" 2>/dev/null || true
        done
        cp -L /lib64/ld-linux-x86-64.so.2 "$tmp_dir/lib64/" 2>/dev/null || true
    fi

    cat << EOF > "$tmp_dir/init"
#!/bin/busybox sh
set -eu
export PATH=/bin:/usr/bin
mount -t proc proc /proc
mount -t sysfs sys /sys
mount -t devtmpfs dev /dev

root_count=0
sshd_count=0
home_count=0

# Installer-created drives place rootfs-A and rootfs-B on partitions 4 and 5; patch both
# because an update may boot either set. Published single-partset images use partition 3.
for dev in /dev/vda3 /dev/vda4 /dev/vda5; do
    [ -b "\$dev" ] || continue
    mkdir -p /mnt
    if mount -t btrfs "\$dev" /mnt 2>/dev/null || mount "\$dev" /mnt 2>/dev/null; then
        if [ ! -f /mnt/etc/os-release ] || ! grep -q '^ID=steamos$' /mnt/etc/os-release; then
            umount /mnt
            continue
        fi

        btrfs_root=0
        if [ -x /bin/btrfs ] && /bin/btrfs property get /mnt ro >/dev/null 2>&1; then
            btrfs_root=1
            if /bin/btrfs property get /mnt ro 2>/dev/null | grep -q 'ro=true'; then
                /bin/btrfs property set /mnt ro false 2>/dev/null || {
                    echo "THEATER_PROVISION_ERROR could not unlock \$dev" > /dev/ttyS0
                    umount /mnt
                    poweroff -f
                    exit 1
                }
            fi
        fi
        mount -o remount,rw /mnt 2>/dev/null || true
        mkdir -p /mnt/etc/sddm.conf.d
        printf "[Autologin]\nSession=plasma.desktop\n" > /mnt/etc/sddm.conf.d/zz-steamos-autologin.conf
        chmod 644 /mnt/etc/sddm.conf.d/zz-steamos-autologin.conf

        mkdir -p /mnt/etc/sudoers.d
        printf "deck ALL=(ALL) NOPASSWD: ALL\n" > /mnt/etc/sudoers.d/zz-deck
        chmod 440 /mnt/etc/sudoers.d/zz-deck

        if [ -f /mnt/usr/lib/systemd/system/sshd.service ]; then
            mkdir -p /mnt/etc/systemd/system/multi-user.target.wants
            ln -sf /usr/lib/systemd/system/sshd.service /mnt/etc/systemd/system/multi-user.target.wants/sshd.service
            sshd_count=\$((sshd_count + 1))
        fi
        sync
        if [ "\$btrfs_root" -eq 1 ]; then
            /bin/btrfs property set /mnt ro true 2>/dev/null || {
                echo "THEATER_PROVISION_ERROR could not relock \$dev" > /dev/ttyS0
                umount /mnt
                poweroff -f
                exit 1
            }
        fi
        umount /mnt
        root_count=\$((root_count + 1))
    fi
done

# Installer-created drives place home on partition 8; published single-partset images use
# partition 5.
for dev in /dev/vda8 /dev/vda5; do
    [ -b "\$dev" ] || continue
    if mount "\$dev" /mnt 2>/dev/null || mount -t btrfs "\$dev" /mnt 2>/dev/null; then
        deck_dir=""
        if [ -d /mnt/deck ]; then
            deck_dir=/mnt/deck
        elif [ -d /mnt/home/deck ]; then
            deck_dir=/mnt/home/deck
        fi
        if [ -n "\$deck_dir" ]; then
            mkdir -p "\$deck_dir/.ssh"
            printf "%s\n" "$pubkey" > "\$deck_dir/.ssh/authorized_keys"
            chmod 700 "\$deck_dir/.ssh"
            chmod 600 "\$deck_dir/.ssh/authorized_keys"
            chown -R 1000:1000 "\$deck_dir/.ssh"
            sync
            umount /mnt
            home_count=1
            break
        fi
        umount /mnt
    fi
done

if [ "\$root_count" -eq 0 ]; then
    echo "THEATER_PROVISION_ERROR no SteamOS root partition was updated" > /dev/ttyS0
    poweroff -f
    exit 1
fi
if [ "\$sshd_count" -ne "\$root_count" ]; then
    echo "THEATER_PROVISION_ERROR sshd was not enabled on every SteamOS root" > /dev/ttyS0
    poweroff -f
    exit 1
fi
if [ "\$home_count" -ne 1 ]; then
    echo "THEATER_PROVISION_ERROR the deck SSH key was not installed" > /dev/ttyS0
    poweroff -f
    exit 1
fi

echo "THEATER_PROVISION_OK" > /dev/ttyS0
poweroff -f
EOF
    chmod 755 "$tmp_dir/init"
    (cd "$tmp_dir" && find . | cpio -H newc -o > "$initramfs") 2>/dev/null

    rm -f "$provision_log"
    set +e
    timeout --foreground --signal=TERM --kill-after=5 60 qemu-system-x86_64 \
        -enable-kvm -cpu host -smp 2 -m 1024 \
        -kernel /boot/vmlinuz-linux \
        -initrd "$initramfs" \
        -append "console=ttyS0 quiet panic=1" \
        -drive if=virtio,format=qcow2,file="$target" \
        -serial file:"$provision_log" -display none -no-reboot
    status=$?
    set -e
    if [ "$status" -ne 0 ] || ! grep -q "THEATER_PROVISION_OK" "$provision_log" 2>/dev/null; then
        [ ! -f "$provision_log" ] || cat "$provision_log" >&2
        die "provisioning failed"
    fi
    note "provisioning complete"
)

cmd_provision() {
    require_image_tools
    prepare_state_dir
    [ -f "$DISK" ] || die "no drive; run: tools/vm/steamos.sh install"
    if [ ! -f "$PERSIST_IMAGE" ]; then
        qemu-img create -q -f qcow2 -F qcow2 -b "$DISK" "$PERSIST_IMAGE"
    fi
    provision_disk "$PERSIST_IMAGE"
}

cmd_install() {
    local written
    require_qemu
    require_image_tools
    validate_settings
    prepare_state_dir
    [ -f "$INSTALLER" ] || cmd_fetch
    new_disk
    qemu-img create -q -f qcow2 "$DISK.part" "$DISK_SIZE"
    rm -f "$INSTALLER_OVERLAY"
    qemu-img create -q -f qcow2 -F raw -b "$INSTALLER" "$INSTALLER_OVERLAY"
    trap 'rm -f "$INSTALLER_OVERLAY" "$DISK.part"' EXIT
    trap 'exit 130' INT TERM

    note "booting the repair image; choose 'Wipe Device & Install SteamOS', then shut down"
    qemu-system-x86_64 \
        -machine "$MACHINE",accel=kvm -cpu host -smp "$CPUS" -m "$MEMORY" \
        -drive if=pflash,format=raw,readonly=on,file="$(ovmf_code)" \
        -drive if=pflash,format=raw,file="$NVRAM" \
        -drive if=virtio,format=qcow2,file="$INSTALLER_OVERLAY" \
        -device nvme,drive=target,serial=theatermode,bootindex=0 \
        -drive if=none,id=target,format=qcow2,file="$DISK.part" \
        -device VGA,xres=1280,yres=800 -display gtk,show-tabs=on \
        -device usb-ehci -device usb-tablet -device usb-kbd \
        -netdev user,id=net0 -device virtio-net-pci,netdev=net0

    qemu-img check -q "$DISK.part" || die "the installed drive failed qemu-img validation"
    written="$(qemu-img info --output=json "$DISK.part" | sed -n 's/.*"actual-size":[[:space:]]*\([0-9]*\).*/\1/p')"
    [ "${written:-0}" -gt 1073741824 ] \
        || die "the drive holds ${written:-0} bytes, so no install was written to it"
    mv -f "$DISK.part" "$DISK"
    printf 'installer %s\n' "$(sed -n 's/^url    //p' "$INSTALLER_ORIGIN")" > "$DISK_ORIGIN"
    trap - EXIT INT TERM
    rm -f "$INSTALLER_OVERLAY"
    note "drive ready: $DISK"

}

cleanup_import() {
    rm -f "$STATE_DIR/import.img" "$STATE_DIR/import.img.zst" "$DISK.part"
}

cmd_import() {
    local want="${1:-$BUILD}" build version url raw digest
    require_image_tools
    command -v zstd >/dev/null || die "zstd is required to import published images"
    validate_settings
    prepare_state_dir
    new_disk
    read -r build version <<<"$(resolve_build "$want")"
    url="$IMAGE_HOST/$VARIANT/$build/$VARIANT-$build-$version.img.zst"
    raw="$STATE_DIR/import.img"
    trap cleanup_import EXIT
    trap 'exit 130' INT TERM
    note "importing $VARIANT $version ($build)"
    note "downloading $url"
    curl -fL --retry 3 --progress-bar -o "$raw.zst" "$url"
    digest="$(sha256sum "$raw.zst" | awk '{ print $1 }')"
    zstd -d --force --quiet -o "$raw" "$raw.zst"
    qemu-img convert -f raw -O qcow2 "$raw" "$DISK.part"
    qemu-img resize -q "$DISK.part" "$DISK_SIZE"
    rm -f "$raw" "$raw.zst"
    mv -f "$DISK.part" "$DISK"
    record_origin "$url" "$digest" "$DISK_ORIGIN"
    trap - EXIT INT TERM
    note "drive ready: $DISK"
    note "this drive has no B partition set; steamos-update needs one made by 'install'"

}

make_run_image() {
    local run_image
    run_image="$(mktemp --tmpdir="$STATE_DIR" steamos-run-XXXXXX.qcow2)"
    rm -f "$run_image"
    qemu-img create -q -f qcow2 -F qcow2 -b "$DISK" "$run_image"
    printf '%s\n' "$run_image"
}

ensure_persist_image() {
    if [ ! -f "$PERSIST_IMAGE" ]; then
        qemu-img create -q -f qcow2 -F qcow2 -b "$DISK" "$PERSIST_IMAGE"
    fi
}

remove_run_image() {
    [ -n "$RUN_IMAGE" ] && [ "$RUN_IMAGE" != "$PERSIST_IMAGE" ] || return 0
    rm -f "$RUN_IMAGE"
}

remove_run_nvram() {
    [ -n "$RUN_NVRAM" ] && [ "$RUN_NVRAM" != "$NVRAM" ] || return 0
    rm -f "$RUN_NVRAM"
}

remove_run_state() {
    remove_run_image
    remove_run_nvram
}

select_run_image() {
    [ -f "$DISK" ] || die "no drive; run: tools/vm/steamos.sh install"
    ensure_nvram
    if [ "$PERSIST" = 1 ]; then
        ensure_persist_image
        RUN_IMAGE="$PERSIST_IMAGE"
        RUN_NVRAM="$NVRAM"
        note "using the persistent overlay; guest changes survive the boot"
    else
        RUN_IMAGE="$(make_run_image)"
        RUN_NVRAM="$(mktemp --tmpdir="$STATE_DIR" steamos-run-XXXXXX.vars)"
        cp "$NVRAM" "$RUN_NVRAM"
        trap remove_run_state EXIT
        trap 'exit 130' INT TERM
    fi
    provision_disk "$RUN_IMAGE"
}

display_devices() {
    local index
    DISPLAY_ARGS=(-device "VGA,id=gpu0,xres=1280,yres=800")
    for ((index = 1; index < OUTPUTS; index++)); do
        DISPLAY_ARGS+=(-device "secondary-vga,id=gpu$index")
    done
}

netdev_spec() {
    local port=$1
    printf 'user,id=net0,hostfwd=tcp:127.0.0.1:%s-:22' "$port"
}

mount_hint() {
    note "mount the checkout in the guest with:"
    note "    sudo mkdir -p /run/theater && sudo mount -t 9p -o trans=virtio,version=9p2000.L,ro theater /run/theater"
}

cmd_run() {
    local port="${SSH_PORT:-2222}"
    require_qemu
    require_image_tools
    validate_settings
    prepare_state_dir
    select_run_image
    display_devices
    note "booting SteamOS with $OUTPUTS virtual display(s)"
    note "guest SSH: ssh -i $SSH_KEY -p $port deck@127.0.0.1 (or: tools/vm/steamos.sh ssh)"
    mount_hint
    qemu-system-x86_64 \
        -machine "$MACHINE",accel=kvm -cpu host -smp "$CPUS" -m "$MEMORY" \
        -drive if=pflash,format=raw,readonly=on,file="$(ovmf_code)" \
        -drive if=pflash,format=raw,file="$RUN_NVRAM" \
        -device nvme,drive=system,serial=theatermode,bootindex=0 \
        -drive if=none,id=system,format=qcow2,file="$RUN_IMAGE" \
        "${DISPLAY_ARGS[@]}" -display gtk,show-tabs=on \
        -device usb-ehci -device usb-tablet -device usb-kbd \
        -netdev "$(netdev_spec "$port")" -device virtio-net-pci,netdev=net0 \
        -virtfs local,path="$REPO_DIR",mount_tag=theater,security_model=mapped-xattr,readonly=on
}

cmd_console() {
    local port="${SSH_PORT:-2222}"
    require_qemu
    require_image_tools
    validate_settings
    prepare_state_dir
    select_run_image
    note "booting with a serial console (Ctrl-A X to exit)"
    note "guest SSH: ssh -i $SSH_KEY -p $port deck@127.0.0.1 (or: tools/vm/steamos.sh ssh)"
    qemu-system-x86_64 \
        -machine "$MACHINE",accel=kvm -cpu host -smp "$CPUS" -m "$MEMORY" \
        -drive if=pflash,format=raw,readonly=on,file="$(ovmf_code)" \
        -drive if=pflash,format=raw,file="$RUN_NVRAM" \
        -device nvme,drive=system,serial=theatermode,bootindex=0 \
        -drive if=none,id=system,format=qcow2,file="$RUN_IMAGE" \
        -netdev "$(netdev_spec "$port")" -device virtio-net-pci,netdev=net0 \
        -virtfs local,path="$REPO_DIR",mount_tag=theater,security_model=mapped-xattr,readonly=on \
        -nographic
}

cmd_ssh() {
    local port="${SSH_PORT:-2222}"
    ensure_ssh_key
    ssh -i "$SSH_KEY" -p "$port" \
        -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
        -o LogLevel=ERROR -o ConnectTimeout=5 \
        deck@127.0.0.1 "$@"
}

qmp() {
    python3 - "$QMP_SOCKET" "$@" <<'PY'
import json
import socket
import sys

path, command = sys.argv[1], sys.argv[2]
arguments = dict(pair.split("=", 1) for pair in sys.argv[3:])

connection = socket.socket(socket.AF_UNIX)
connection.connect(path)
stream = connection.makefile("rw", encoding="utf-8", newline="\n")

def call(name, **kwargs):
    request = {"execute": name}
    if kwargs:
        request["arguments"] = kwargs
    stream.write(json.dumps(request) + "\n")
    stream.flush()
    while True:
        reply = json.loads(stream.readline())
        if "event" in reply:
            continue
        if "error" in reply:
            raise SystemExit(f"qmp {name}: {reply['error']['desc']}")
        return reply["return"]

stream.readline()
call("qmp_capabilities")
call(command, **arguments)
PY
}

stop_guest() {
    local deadline
    if [ -n "$QEMU_PID" ] && kill -0 "$QEMU_PID" 2>/dev/null; then
        kill "$QEMU_PID" 2>/dev/null || true
    fi
    deadline=$((SECONDS + 30))
    while [ -n "$QEMU_PID" ] && process_running "$QEMU_PID" \
        && [ "$SECONDS" -lt "$deadline" ]; do
        sleep 0.05
    done
    if [ -n "$QEMU_PID" ] && kill -0 "$QEMU_PID" 2>/dev/null; then
        kill -9 "$QEMU_PID" 2>/dev/null || true
    fi
    [ -z "$QEMU_PID" ] || wait "$QEMU_PID" 2>/dev/null || true
    QEMU_PID=""
}

screenshot_cleanup() {
    stop_guest
    rm -f "$QMP_SOCKET"
    remove_run_state
}

cmd_screenshot() {
    local out="${1:-$STATE_DIR/screenshot.png}" waited=0 port
    port="${SSH_PORT:-2222}"
    require_qemu
    require_image_tools
    validate_settings
    prepare_state_dir
    select_run_image
    trap screenshot_cleanup EXIT
    trap 'exit 130' INT TERM
    rm -f "$QMP_SOCKET"
    display_devices

    note "booting headless; capturing after ${SETTLE}s"
    qemu-system-x86_64 \
        -machine "$MACHINE",accel=kvm -cpu host -smp "$CPUS" -m "$MEMORY" \
        -drive if=pflash,format=raw,readonly=on,file="$(ovmf_code)" \
        -drive if=pflash,format=raw,file="$RUN_NVRAM" \
        -device nvme,drive=system,serial=theatermode,bootindex=0 \
        -drive if=none,id=system,format=qcow2,file="$RUN_IMAGE" \
        "${DISPLAY_ARGS[@]}" -display none \
        -netdev "$(netdev_spec "$port")" -device virtio-net-pci,netdev=net0 \
        -qmp "unix:$QMP_SOCKET,server=on,wait=off" &
    QEMU_PID=$!

    while [ ! -S "$QMP_SOCKET" ]; do
        waited=$((waited + 1))
        [ "$waited" -le 30 ] || die "qemu did not open its QMP socket"
        sleep 1
    done
    sleep "$SETTLE"
    qmp screendump "filename=$out" format=png
    note "captured $out"
}

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
        sleep 1
    done
    die "the guest did not present a graphical session within ${CHECK_TIMEOUT}s"
}

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

assert_guest_readonly() {
    local port=$1
    guest_ssh "$port" bash -s <<'GUEST'
set -euo pipefail

[ "$(steamos-readonly status)" = enabled ] || {
    echo "SteamOS root protection is not enabled." >&2
    exit 1
}
if [ "$(findmnt -no FSTYPE /)" = btrfs ]; then
    sudo btrfs property get / ro | grep -qx 'ro=true' || {
        echo "The SteamOS Btrfs root is not read-only." >&2
        exit 1
    }
fi
GUEST
    note "guest root protection is enabled"
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

[ "$activated" -eq 1 ] || {
    echo "The daemon did not report an active effect across the other outputs." >&2
    exit 1
}
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

check_cleanup() {
    stop_guest
    remove_run_state
}

cmd_check() {
    local port status dimmer art
    dimmer="$REPO_DIR/src/theater_mode/dimmer/theater-dimmer"
    art="$REPO_DIR/src/theater_mode/art/theater-art"
    if [ ! -x "$dimmer" ] || [ ! -x "$art" ]; then
        die "helpers not built; run: make -C src/theater_mode/dimmer && make -C src/theater_mode/art"
    fi

    require_qemu
    require_image_tools
    validate_settings
    prepare_state_dir
    select_run_image
    display_devices
    ensure_ssh_key
    port="${SSH_PORT:-$(free_port)}"
    trap check_cleanup EXIT
    trap 'exit 130' INT TERM

    note "booting SteamOS headless with $OUTPUTS virtual display(s) on port $port"
    qemu-system-x86_64 \
        -machine "$MACHINE",accel=kvm -cpu host -smp "$CPUS" -m "$MEMORY" \
        -drive if=pflash,format=raw,readonly=on,file="$(ovmf_code)" \
        -drive if=pflash,format=raw,file="$RUN_NVRAM" \
        -device nvme,drive=system,serial=theatermode,bootindex=0 \
        -drive if=none,id=system,format=qcow2,file="$RUN_IMAGE" \
        "${DISPLAY_ARGS[@]}" -display none \
        -netdev "$(netdev_spec "$port")" -device virtio-net-pci,netdev=net0 \
        -virtfs local,path="$REPO_DIR",mount_tag=theater,security_model=mapped-xattr,readonly=on &
    QEMU_PID=$!

    wait_for_guest "$port"
    assert_guest_displays "$port" "$OUTPUTS"
    assert_guest_readonly "$port"

    note "installing the checkout in the guest and running doctor"
    set +e
    guest_ssh "$port" bash -s <<'GUEST'
set -euo pipefail

export XDG_RUNTIME_DIR="/run/user/$(id -u)"
export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"
published="$(systemctl --user show-environment 2>/dev/null || true)"
for key in XDG_SESSION_TYPE XDG_CURRENT_DESKTOP WAYLAND_DISPLAY DISPLAY; do
    val="$(grep -E "^$key=" <<< "$published" | head -1 | cut -d= -f2- || true)"
    [ -n "$val" ] && export "$key=$val"
done

sudo mkdir -p /run/theater
sudo mount -t 9p -o trans=virtio,version=9p2000.L,ro theater /run/theater 2>/dev/null || true

/run/theater/install.sh \
    --dimmer-bin=/run/theater/src/theater_mode/dimmer/theater-dimmer \
    --art-bin=/run/theater/src/theater_mode/art/theater-art

~/.local/bin/theater-mode doctor
GUEST
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
    [ -d "$STATE_DIR" ] || die "nothing cached at $STATE_DIR"
    if [ -f "$INSTALLER" ]; then
        note "repair image: $INSTALLER"
        sed 's/^/    /' "$INSTALLER_ORIGIN"
    else
        note "no repair image cached"
    fi
    if [ -f "$DISK" ]; then
        note "unmodified base drive: $DISK"
        [ ! -f "$DISK_ORIGIN" ] || sed 's/^/    /' "$DISK_ORIGIN"
        qemu-img check -q "$DISK" || die "the drive failed qemu-img validation"
        qemu-img info "$DISK"
    else
        note "no drive; run: tools/vm/steamos.sh install"
    fi
    if [ -f "$PERSIST_IMAGE" ]; then
        note "persistent overlay: $PERSIST_IMAGE"
        qemu-img check -q "$PERSIST_IMAGE" || die "the persistent overlay failed qemu-img validation"
    fi
    report_stale_overlays
}

report_stale_overlays() {
    local overlays
    overlays="$(find "$STATE_DIR" -maxdepth 1 \
        \( -name 'steamos-run-*.qcow2' -o -name 'steamos-run-*.vars' \) -print 2>/dev/null)"
    [ -n "$overlays" ] || return 0
    note "stale run overlays are present; remove them with: tools/vm/steamos.sh clean"
    printf '%s\n' "$overlays" | sed 's/^/    /'
}

cmd_clean() {
    validate_state_dir
    if [ ! -e "$STATE_DIR" ]; then
        note "no SteamOS state directory exists at $STATE_DIR"
        return 0
    fi
    if [ ! -f "$STATE_MARKER" ] && [ "$STATE_DIR" != "$(realpath -m -- "$DEFAULT_STATE_ROOT/steamos")" ]; then
        if [ -n "$(find "$STATE_DIR" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]; then
            die "refusing to clean an unrecognized state directory without $STATE_MARKER"
        fi
        note "no generated SteamOS state exists at $STATE_DIR"
        return 0
    fi
    note "removing generated SteamOS state under $STATE_DIR"
    rm -f "$DISK" "$DISK.part" "$DISK_ORIGIN" "$PERSIST_IMAGE" "$NVRAM" \
        "$INSTALLER" "$INSTALLER.part" "$INSTALLER_ORIGIN" "$INSTALLER_OVERLAY" \
        "$STATE_DIR/import.img" "$STATE_DIR/import.img.zst" \
        "$STATE_DIR/screenshot.png" "$STATE_DIR/provision.log" \
        "$QMP_SOCKET" "$STATE_MARKER"
    find "$STATE_DIR" -maxdepth 1 -name '*.part' -delete 2>/dev/null || true
    find "$STATE_DIR" -maxdepth 1 -name 'steamos-run-*.qcow2' -delete 2>/dev/null || true
    find "$STATE_DIR" -maxdepth 1 -name 'steamos-run-*.vars' -delete 2>/dev/null || true
    find "$STATE_DIR" -maxdepth 1 -name 'provision-*.cpio' -delete 2>/dev/null || true
    find "$STATE_DIR" -maxdepth 1 -name '*.img.zip*' -delete 2>/dev/null || true
}

command="${1:-}"
shift || true
case "$command" in
    ssh) cmd_ssh "$@" ;;
    builds|import|screenshot)
        [ $# -le 1 ] || die "$command accepts at most one argument"
        "cmd_$command" "$@"
        ;;
    fetch|install|provision|run|check|console|inspect|clean)
        [ $# -eq 0 ] || die "$command accepts no arguments"
        "cmd_$command"
        ;;
    -h|--help|"") usage ;;
    *) die "unknown command: $command (try --help)" ;;
esac
