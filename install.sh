#!/usr/bin/env bash
#
# Install theater-mode into the user's home ($HOME) without elevation.
#
#   ./install.sh            copy the files into place
#   ./install.sh --link     symlink them instead, so edits in this repo are live
#   ./install.sh --uninstall  remove what this script installed
#
# Re-running is safe: it overwrites its own files and leaves your settings
# drop-in and generated artwork untouched.

set -euo pipefail

REPO="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

BIN_DIR="${XDG_BIN_HOME:-$HOME/.local/bin}"
DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}"
CONF_DIR="${XDG_CONFIG_HOME:-$HOME/.config}"

DIMMER_BIN="$BIN_DIR/theater-dimmer"
DAEMON="$BIN_DIR/theater-moded"
KWIN_SCRIPT="$DATA_DIR/kwin/scripts/theater-detect"
UNIT="$CONF_DIR/systemd/user/theater-mode.service"
DOC="$DATA_DIR/theater-mode/README.md"

MODE=copy

# --------------------------------------------------------------------------

die() { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }
info() { printf '  %s\n' "$*"; }

# Every install goes through here so copy and symlink modes cannot drift apart.
place() {
    local src=$1 dest=$2
    [ -e "$src" ] || die "missing from repo: $src"
    mkdir -p "$(dirname "$dest")" || die "could not create $(dirname "$dest")"
    rm -rf "$dest"
    if [ "$MODE" = link ]; then
        ln -s "$src" "$dest" || die "could not link $dest"
    else
        cp -r "$src" "$dest" || die "could not copy to $dest"
    fi
    info "$dest"
}

check_prerequisites() {
    local missing=()
    command -v gcc >/dev/null || command -v cc >/dev/null || missing+=("c compiler (gcc/clang)")
    command -v make >/dev/null || missing+=("make")
    command -v pkg-config >/dev/null || missing+=("pkg-config")
    command -v systemctl >/dev/null || missing+=("systemctl")
    python3 -c 'import gi; gi.require_version("Gio", "2.0")' 2>/dev/null \
        || missing+=("python3 gobject bindings (python3-gobject)")

    # Check libwayland client development headers required to compile theater-dimmer
    if command -v pkg-config >/dev/null; then
        pkg-config --exists wayland-client \
            || missing+=("libwayland client development files (wayland-devel / libwayland-dev)")
    fi

    if [ ${#missing[@]} -gt 0 ]; then
        printf 'missing prerequisites:\n' >&2
        printf '  - %s\n' "${missing[@]}" >&2
        die "please install missing dependencies"
    fi

    # Pillow is optional; without it, secondary screens dim to plain black without artwork
    python3 -c 'import PIL' 2>/dev/null || cat >&2 <<'EOF'
warning: python3-pillow is not installed. Game artwork cannot be generated,
         so secondary displays will dim to plain black.
EOF
}

do_install() {
    check_prerequisites

    echo "Building theater-dimmer:"
    make -C "$REPO/src/theater_mode/dimmer"

    echo "Installing theater-mode ($MODE mode):"
    place "$REPO/src/theater_mode/dimmer/theater-dimmer" "$DIMMER_BIN"
    chmod +x "$DIMMER_BIN"
    place "$REPO/bin/theater-moded" "$DAEMON"
    chmod +x "$DAEMON"
    place "$REPO/src/theater_mode" "$DATA_DIR/theater-mode/lib/theater_mode"
    place "$REPO/kwin/theater-detect" "$KWIN_SCRIPT"
    place "$REPO/systemd/theater-mode.service" "$UNIT"
    place "$REPO/README.md" "$DOC"

    systemctl --user daemon-reload || die "systemctl daemon-reload failed"
    systemctl --user enable theater-mode.service >/dev/null 2>&1 \
        || die "could not enable theater-mode.service"
    systemctl --user restart theater-mode.service || die "could not start theater-mode.service"

    cat <<EOF

theater-mode installed.

Next steps:
  1. Enable the KWin script:
     System Settings -> Window Management -> KWin Scripts -> "Theater Mode Detector"

  2. Configure an effect (default is dry-run 'log'):
     systemctl --user edit theater-mode.service

     [Service]
     Environment=THEATER_EFFECT=dim

     systemctl --user restart theater-mode.service

Logs:          journalctl --user -u theater-mode.service -f
Documentation: $DOC
EOF
}

do_uninstall() {
    echo "The following will be removed:"
    local targets=("$DIMMER_BIN" "$DAEMON" "$KWIN_SCRIPT" "$UNIT" "$DOC" "$DATA_DIR/theater-mode/lib")
    local found=0
    for t in "${targets[@]}"; do
        if [ -e "$t" ] || [ -L "$t" ]; then
            info "$t"
            found=$((found + 1))
        fi
    done
    if [ "$found" -eq 0 ]; then
        echo "  (nothing installed)"
        return 0
    fi

    echo
    echo "Settings drop-ins and cache directories will not be removed:"
    info "$CONF_DIR/systemd/user/theater-mode.service.d/"
    info "${XDG_CACHE_HOME:-$HOME/.cache}/theater-mode/"
    echo
    read -r -p "Remove the $found item(s) above? [y/N] " reply
    [ "$reply" = y ] || [ "$reply" = Y ] || { echo "Cancelled."; return 0; }

    systemctl --user disable --now theater-mode.service >/dev/null 2>&1 || true
    for t in "${targets[@]}"; do
        rm -rf "$t"
    done
    systemctl --user daemon-reload || true

    echo
    echo "Uninstalled. Remember to disable \"Theater Mode Detector\" in System Settings -> KWin Scripts."
}

case "${1:-}" in
    "")           do_install ;;
    --link)       MODE=link; do_install ;;
    --uninstall)  do_uninstall ;;
    -h|--help)    sed -n '3,10p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' ;;
    *)            die "unknown option: $1 (try --help)" ;;
esac
