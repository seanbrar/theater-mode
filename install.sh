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
CLIENT="$BIN_DIR/theater-mode"
KWIN_SCRIPT="$DATA_DIR/kwin/scripts/theater-detect"
UNIT="$CONF_DIR/systemd/user/theater-mode.service"
DOC="$DATA_DIR/theater-mode/README.md"
REF_CONFIG="$DATA_DIR/theater-mode/config.reference.toml"
APP_DATA="$DATA_DIR/theater-mode"

MODE="copy"
FORCE=0
SERVICE=1  # 1 = enable/start systemd service; 0 (--no-service) = stage files only

# --------------------------------------------------------------------------

die()  { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }
warn() { printf '\033[33mwarning:\033[0m %s\n' "$*" >&2; }
info() { printf '  %s\n' "$*"; }

show_help() {
    cat <<'EOF'
Install theater-mode into the user's home ($HOME) without elevation.

Usage:
  ./install.sh [OPTIONS]

Options:
  -l, --link       Symlink files into place instead of copying (for development)
  -u, --uninstall  Remove installed binaries, KWin script, and systemd unit
  -n, --no-service Stage files only; do not reload, enable, or start the user service
  -y, --yes        Answer yes to uninstall confirmation prompts non-interactively
  -h, --help       Show this help message
EOF
}

# Every install goes through here so copy and symlink modes cannot drift apart.
place() {
    local src=$1 dest=$2
    [ -e "$src" ] || die "missing from repo: $src"
    mkdir -p "$(dirname "$dest")" || die "could not create $(dirname "$dest")"
    rm -rf "$dest"
    if [ "$MODE" = "link" ]; then
        ln -s "$src" "$dest" || die "could not link $dest"
    else
        cp -r "$src" "$dest" || die "could not copy to $dest"
    fi
    info "$dest"
}

# Render systemd unit template with the target daemon binary path.
render_unit() {
    local src="$REPO/systemd/theater-mode.service"
    local escaped tmp line rendered=0
    [ -e "$src" ] || die "missing from repo: $src"
    mkdir -p "$(dirname "$UNIT")" || die "could not create $(dirname "$UNIT")"
    case "$DAEMON" in
        *$'\n'*|*$'\r'*) die "XDG_BIN_HOME must not contain newlines" ;;
    esac

    # Quote systemd syntax without passing the path through a text-substitution language.
    escaped=${DAEMON//\\/\\\\}
    escaped=${escaped//\"/\\\"}
    escaped=${escaped//%/%%}
    escaped=${escaped//\$/\$\$}
    tmp=$(mktemp "${UNIT}.tmp.XXXXXX") || die "could not create temporary unit"
    if ! while IFS= read -r line || [ -n "$line" ]; do
        if [ "$line" = 'ExecStart=/usr/bin/env -- "@DAEMON@"' ]; then
            printf 'ExecStart=/usr/bin/env -- "%s"\n' "$escaped"
            rendered=$((rendered + 1))
        else
            printf '%s\n' "$line"
        fi
    done < "$src" > "$tmp"; then
        rm -f "$tmp"
        die "could not render $UNIT"
    fi
    if [ "$rendered" -ne 1 ]; then
        rm -f "$tmp"
        die "expected exactly one daemon placeholder in $src"
    fi
    mv -f "$tmp" "$UNIT" || { rm -f "$tmp"; die "could not write $UNIT"; }
    info "$UNIT"
}

# Remove dimmer source/binary and bytecode cache from copy-installed lib directory.
prune_package_copy() {
    if [ "$MODE" = "link" ]; then
        return 0
    fi
    rm -rf "$APP_DATA/lib/theater_mode/dimmer"
    find "$APP_DATA/lib/theater_mode" -type d -name __pycache__ -prune -exec rm -rf {} +
}

check_prerequisites() {
    local missing=()
    command -v python3 >/dev/null || missing+=("python3 (>= 3.12)")
    if command -v python3 >/dev/null; then
        python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' 2>/dev/null \
            || missing+=("python3 >= 3.12 (found $(python3 -V 2>&1))")
    fi
    command -v gcc >/dev/null || command -v cc >/dev/null || missing+=("c compiler (gcc/clang)")
    command -v make >/dev/null || missing+=("make")
    command -v pkg-config >/dev/null || missing+=("pkg-config")
    if [ "$SERVICE" -eq 1 ]; then
        command -v systemctl >/dev/null || missing+=("systemctl")
    fi

    if command -v python3 >/dev/null; then
        python3 -c 'import gi; gi.require_version("Gio", "2.0"); gi.require_version("GLib", "2.0")' 2>/dev/null \
            || missing+=("python3 gobject bindings (python3-gobject / python-gobject)")
    fi

    # Check libwayland client development headers required to compile theater-dimmer
    if command -v pkg-config >/dev/null; then
        pkg-config --exists wayland-client \
            || missing+=("libwayland client development files (wayland-devel / libwayland-dev / wayland)")
    fi

    if [ ${#missing[@]} -gt 0 ]; then
        printf '\033[31merror:\033[0m missing prerequisites:\n' >&2
        printf '  - %s\n' "${missing[@]}" >&2
        die "please install missing dependencies"
    fi

    # Check if target bin directory is in user PATH
    if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
        warn "$BIN_DIR is not in your PATH. You may need to add it to your ~/.bashrc or ~/.profile."
    fi

    # Pillow is optional; without it, secondary screens dim to plain black without artwork
    if command -v python3 >/dev/null && ! python3 -c 'import PIL' 2>/dev/null; then
        warn "python3-pillow is not installed. Game artwork cannot be generated,"
        warn "         so secondary displays will dim to plain black."
    fi
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
    place "$REPO/bin/theater-mode" "$CLIENT"
    chmod +x "$CLIENT"
    place "$REPO/src/theater_mode" "$APP_DATA/lib/theater_mode"
    prune_package_copy
    place "$REPO/kwin/theater-detect" "$KWIN_SCRIPT"
    render_unit
    place "$REPO/README.md" "$DOC"

    # The reference config is generated from the schema, never hand-maintained.
    mkdir -p "$(dirname "$REF_CONFIG")"
    PYTHONPATH="$REPO/src" python3 -c \
        'import sys
from theater_mode.config import generate_reference_config
sys.stdout.write(generate_reference_config())' > "$REF_CONFIG" \
        || die "failed to generate $REF_CONFIG"

    if [ "$SERVICE" -eq 0 ]; then
        cat <<EOF

theater-mode staged successfully (--no-service: nothing was activated).

The unit is installed but not enabled or started. To activate it:
  systemctl --user daemon-reload
  systemctl --user enable --now theater-mode.service

Reference:     $REF_CONFIG
EOF
        return 0
    fi

    systemctl --user daemon-reload || die "systemctl daemon-reload failed"
    systemctl --user enable theater-mode.service >/dev/null 2>&1 \
        || die "could not enable theater-mode.service"
    systemctl --user restart theater-mode.service || die "could not start theater-mode.service"

    cat <<EOF

theater-mode installed successfully.

Next steps:
  1. Enable the KWin script:
     System Settings -> Window Management -> KWin Scripts -> "Theater Mode Detector"

  2. theater-mode works immediately with built-in defaults (cinematic dimming active).

  3. Inspect or modify configuration live via the CLI:
     theater-mode config show
     theater-mode config set effect.dim_factor 0.75

Logs:          journalctl --user -u theater-mode.service -f
Reference:     $REF_CONFIG
EOF
}

do_uninstall() {
    echo "The following will be removed:"
    local targets=("$DIMMER_BIN" "$DAEMON" "$CLIENT" "$KWIN_SCRIPT" "$UNIT" "$APP_DATA")
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

    if [ "$FORCE" -eq 0 ]; then
        read -r -p "Remove the $found item(s) above? [y/N] " reply
        [ "$reply" = y ] || [ "$reply" = Y ] || { echo "Cancelled."; return 0; }
    fi

    if [ "$SERVICE" -eq 1 ]; then
        systemctl --user disable --now theater-mode.service >/dev/null 2>&1 || true
    fi
    rm -rf "${targets[@]}"
    if [ "$SERVICE" -eq 1 ]; then
        systemctl --user daemon-reload || true
    fi

    # Attempt to notify KWin if running
    if command -v busctl >/dev/null 2>&1; then
        busctl --user call org.kde.KWin /Scripting org.kde.kwin.Scripting unloadScript s "theater-detect" >/dev/null 2>&1 || true
    elif command -v qdbus6 >/dev/null 2>&1; then
        qdbus6 org.kde.KWin /Scripting org.kde.kwin.Scripting.unloadScript theater-detect >/dev/null 2>&1 || true
    elif command -v qdbus >/dev/null 2>&1; then
        qdbus org.kde.KWin /Scripting org.kde.kwin.Scripting.unloadScript theater-detect >/dev/null 2>&1 || true
    fi

    echo
    echo "Uninstalled. Remember to disable \"Theater Mode Detector\" in System Settings -> KWin Scripts."
}

ACTION="install"
while [ $# -gt 0 ]; do
    case "$1" in
        -l|--link)       MODE="link" ;;
        -u|--uninstall)  ACTION="uninstall" ;;
        -n|--no-service) SERVICE=0 ;;
        -y|--yes|-f|--force) FORCE=1 ;;
        -h|--help)       show_help; exit 0 ;;
        *)               die "unknown option: $1 (try --help)" ;;
    esac
    shift
done

if [ "$ACTION" = "uninstall" ]; then
    do_uninstall
else
    do_install
fi
