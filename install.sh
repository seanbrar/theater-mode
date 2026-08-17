#!/usr/bin/env bash
#
# Install theater-mode into the user's home. Touches nothing outside $HOME and
# needs no elevation — the atomic base system is deliberately left alone.
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
    command -v kscreen-doctor >/dev/null || missing+=("kscreen-doctor (kscreen)")
    command -v systemctl >/dev/null || missing+=("systemctl")
    python3 -c 'import gi; gi.require_version("Gio", "2.0")' 2>/dev/null \
        || missing+=("python3 gobject bindings (python3-gobject)")
    if [ ${#missing[@]} -gt 0 ]; then
        printf 'missing prerequisites:\n' >&2
        printf '  - %s\n' "${missing[@]}" >&2
        die "install those first"
    fi
}

do_install() {
    check_prerequisites

    echo "Installing theater-mode ($MODE):"
    place "$REPO/bin/theater-moded" "$DAEMON"
    chmod +x "$REPO/bin/theater-moded"
    place "$REPO/kwin/theater-detect" "$KWIN_SCRIPT"
    place "$REPO/systemd/theater-mode.service" "$UNIT"
    place "$REPO/README.md" "$DOC"

    systemctl --user daemon-reload || die "systemctl daemon-reload failed"
    systemctl --user enable theater-mode.service >/dev/null 2>&1 \
        || die "could not enable theater-mode.service"
    systemctl --user restart theater-mode.service || die "could not start theater-mode.service"

    cat <<EOF

Installed. The daemon is running in dry-run mode: it logs what it would do and
changes nothing until you ask it to.

Two steps left, both yours:

  1. Enable the detector:
       System Settings -> Window Management -> KWin Scripts -> "Theater Mode Detector"

  2. Turn on a real effect:
       systemctl --user edit theater-mode.service

     add:
       [Service]
       Environment=THEATER_EFFECT=brightness,wallpaper

     then:
       systemctl --user restart theater-mode.service

Watch it work:  journalctl --user -u theater-mode.service -f
Settings:       $DOC
EOF
}

do_uninstall() {
    echo "The following will be removed:"
    local targets=("$DAEMON" "$KWIN_SCRIPT" "$UNIT" "$DOC")
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
    echo "Your settings drop-in, cached artwork and saved state are NOT touched:"
    info "$CONF_DIR/systemd/user/theater-mode.service.d/"
    info "${XDG_CACHE_HOME:-$HOME/.cache}/theater-mode/"
    info "${XDG_STATE_HOME:-$HOME/.local/state}/theater-mode/"
    echo
    read -r -p "Remove the $found item(s) above? [y/N] " reply
    [ "$reply" = y ] || [ "$reply" = Y ] || { echo "Cancelled."; return 0; }

    systemctl --user disable --now theater-mode.service >/dev/null 2>&1 || true
    for t in "${targets[@]}"; do
        rm -rf "$t"
    done
    systemctl --user daemon-reload || true

    echo
    echo "Removed. Disable \"Theater Mode Detector\" in System Settings -> KWin Scripts to finish."
}

case "${1:-}" in
    "")           do_install ;;
    --link)       MODE=link; do_install ;;
    --uninstall)  do_uninstall ;;
    -h|--help)    sed -n '3,12p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' ;;
    *)            die "unknown option: $1 (try --help)" ;;
esac
