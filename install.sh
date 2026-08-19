#!/usr/bin/env bash
#
# Install theater-mode into the user's home ($HOME) without elevation.
#
#   ./install.sh              copy the files into place
#   ./install.sh --uninstall  remove what this script installed
#
# Re-running is safe: it overwrites its own files and leaves your settings
# drop-in and generated artwork untouched.

set -euo pipefail

REPO="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_REPO="seanbrar/theater-mode"

BIN_DIR="${XDG_BIN_HOME:-$HOME/.local/bin}"
DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}"
CONF_DIR="${XDG_CONFIG_HOME:-$HOME/.config}"

DIMMER_BIN="$BIN_DIR/theater-dimmer"
ART_BIN="$BIN_DIR/theater-art"
DAEMON="$BIN_DIR/theater-moded"
CLIENT="$BIN_DIR/theater-mode"
KWIN_SCRIPT="$DATA_DIR/kwin/scripts/theater-detect"
UNIT="$CONF_DIR/systemd/user/theater-mode.service"
KWIN_PLUGIN_ID="theater-detect"
DOC="$DATA_DIR/theater-mode/README.md"
REF_CONFIG="$DATA_DIR/theater-mode/config.reference.toml"
APP_DATA="$DATA_DIR/theater-mode"
SELF_COPY="$DATA_DIR/theater-mode/install.sh"

FORCE=0
SERVICE=1  # 0 stages files, 1 activates a new install, 2 preserves activation state

PREBUILT_DIMMER="$REPO/bin/theater-dimmer"
BUILT_DIMMER="$REPO/src/theater_mode/dimmer/theater-dimmer"
DIMMER_OVERRIDE=""
PREBUILT_ART="$REPO/bin/theater-art"
BUILT_ART="$REPO/src/theater_mode/art/theater-art"
ART_OVERRIDE=""
FORCE_BUILD=0
DIMMER_SOURCE=""
ART_SOURCE=""
NEEDS_DIMMER_BUILD=0
NEEDS_ART_BUILD=0

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
  -u, --uninstall  Remove installed binaries, KWin script, and systemd unit
  -n, --no-service Stage files only; do not activate the service or the KWin script
      --preserve-service
                   Replace files and restart the service only if it is already running
      --dimmer-bin=PATH
                   Install this prebuilt theater-dimmer instead of compiling one
      --art-bin=PATH
                   Install this prebuilt theater-art instead of compiling one
  -b, --build      Compile helper binaries from source, ignoring prebuilt helpers
  -y, --yes        Answer yes to uninstall confirmation prompts non-interactively
  -h, --help       Show this help message
EOF
}

# Discard a staging directory that may hold read-only copies of a read-only source tree.
discard() {
    chmod -R u+w "$1" 2>/dev/null || true
    rm -rf "$1"
}

place() {
    local src=$1 dest=$2 parent stage had_old=0
    [ -e "$src" ] || die "missing from repo: $src"
    parent=$(dirname "$dest")
    mkdir -p "$parent" || die "could not create $parent"
    stage=$(mktemp -d "$parent/.theater-mode-install.XXXXXX") \
        || die "could not stage $dest"
    cp -a "$src" "$stage/new" || { discard "$stage"; die "could not stage $dest"; }
    # cp -a preserves source modes, so a read-only checkout would otherwise install a
    # read-only tree that neither the next upgrade nor --uninstall can remove.
    chmod -R u+w "$stage/new" 2>/dev/null || true
    if [ -e "$dest" ] || [ -L "$dest" ]; then
        mv "$dest" "$stage/old" || { discard "$stage"; die "could not replace $dest"; }
        had_old=1
    fi
    if ! mv "$stage/new" "$dest"; then
        [ "$had_old" -eq 1 ] && mv "$stage/old" "$dest" || true
        discard "$stage"
        die "could not install $dest"
    fi
    discard "$stage"
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

# Remove native-helper sources and bytecode caches from the installed library.
prune_package_copy() {
    rm -rf "$APP_DATA/lib/theater_mode/dimmer" "$APP_DATA/lib/theater_mode/art"
    find "$APP_DATA/lib/theater_mode" -type d -name __pycache__ -prune -exec rm -rf {} +
}

# Persist the same KWin setting used by System Settings.
activate_kwin_script() {
    command -v kwriteconfig6 >/dev/null 2>&1 || return 1
    kwriteconfig6 --file kwinrc --group Plugins \
        --key "${KWIN_PLUGIN_ID}Enabled" true >/dev/null 2>&1 || return 1
    notify_kwin_reconfigure
    return 0
}

deactivate_kwin_script() {
    command -v kwriteconfig6 >/dev/null 2>&1 || return 1
    kwriteconfig6 --file kwinrc --group Plugins \
        --key "${KWIN_PLUGIN_ID}Enabled" --delete >/dev/null 2>&1 || return 1
    return 0
}

notify_kwin_reconfigure() {
    if command -v busctl >/dev/null 2>&1; then
        busctl --user call org.kde.KWin /KWin org.kde.KWin reconfigure >/dev/null 2>&1 || true
    elif command -v qdbus6 >/dev/null 2>&1; then
        qdbus6 org.kde.KWin /KWin reconfigure >/dev/null 2>&1 || true
    elif command -v qdbus >/dev/null 2>&1; then
        qdbus org.kde.KWin /KWin reconfigure >/dev/null 2>&1 || true
    fi
}

resolve_helper() {
    local kind=$1 override=$2 prebuilt=$3 built=$4
    local source_var=$5 needs_build_var=$6 source needs_build

    if [ "$FORCE_BUILD" -eq 1 ]; then
        source=$built
        needs_build=1
    elif [ -n "$override" ]; then
        [ -e "$override" ] || die "--$kind-bin: file not found: $override"
        [ -x "$override" ] \
            || die "--$kind-bin: binary is not executable: $override (try: chmod +x '$override')"
        source=$override
        needs_build=0
    elif [ -f "$prebuilt" ]; then
        source=$prebuilt
        needs_build=0
    else
        source=$built
        needs_build=1
    fi

    printf -v "$source_var" '%s' "$source"
    printf -v "$needs_build_var" '%s' "$needs_build"
}

verify_helper() {
    local kind=$1 source=$2 candidate=$2 staged="" out status
    # tar applies the caller's umask, so an archive can arrive without its execute bit.
    # Verify a writable copy: the release tree it came from may be read-only.
    if [ ! -x "$candidate" ]; then
        staged=$(mktemp) || die "could not stage $source for verification"
        if ! cp "$source" "$staged"; then
            rm -f "$staged"
            die "could not stage $source for verification"
        fi
        chmod +x "$staged"
        candidate=$staged
    fi
    out=$("$candidate" --version 2>&1) && status=0 || status=$?
    if [ -n "$staged" ]; then
        rm -f "$staged"
    fi
    if [ "$status" -ne 0 ]; then
        printf '\033[31merror:\033[0m the theater-%s helper cannot run on this system:\n' "$kind" >&2
        printf '  %s\n' "$out" >&2
        case "$out" in
            *GLIBC_*)
                printf '  This build targets a newer glibc than this system provides.\n' >&2
                printf '  Compile one for this machine instead: ./install.sh --build\n' >&2
                ;;
            *libwayland-client*)
                printf '  libwayland-client.so.0 is missing. Install your distribution wayland runtime.\n' >&2
                ;;
        esac
        exit 1
    fi
    info "$out ($source)"
}

check_prerequisites() {
    local missing=()
    command -v python3 >/dev/null || missing+=("python3 (>= 3.12)")
    if command -v python3 >/dev/null; then
        python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' 2>/dev/null \
            || missing+=("python3 >= 3.12 (found $(python3 -V 2>&1))")
    fi
    if [ "$NEEDS_DIMMER_BUILD" -eq 1 ] || [ "$NEEDS_ART_BUILD" -eq 1 ]; then
        command -v gcc >/dev/null || command -v cc >/dev/null || missing+=("c compiler (gcc/clang)")
        command -v make >/dev/null || missing+=("make")
    fi
    if [ "$NEEDS_DIMMER_BUILD" -eq 1 ]; then
        command -v pkg-config >/dev/null || missing+=("pkg-config")
    fi
    if [ "$SERVICE" -ne 0 ]; then
        command -v systemctl >/dev/null || missing+=("systemctl")
    fi

    if command -v python3 >/dev/null; then
        python3 -c 'import gi; gi.require_version("Gio", "2.0"); gi.require_version("GLib", "2.0")' 2>/dev/null \
            || missing+=("python3 gobject bindings (python3-gobject / python-gobject)")
    fi

    # Wayland development files are a build-time need only. Installing a prebuilt helper
    # requires just the runtime library, which every Wayland session already has.
    if [ "$NEEDS_DIMMER_BUILD" -eq 1 ] && command -v pkg-config >/dev/null; then
        pkg-config --exists wayland-client \
            || missing+=("libwayland client development files (wayland-devel / libwayland-dev / wayland)")
    fi

    if [ ${#missing[@]} -gt 0 ]; then
        printf '\033[31merror:\033[0m missing prerequisites:\n' >&2
        printf '  - %s\n' "${missing[@]}" >&2
        printf "\n  Please install the missing dependencies with your package manager.\n" >&2
        exit 1
    fi

    # Check if target bin directory is in user PATH
    if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
        warn "$BIN_DIR is not in your PATH. You may need to add it to your ~/.bashrc or ~/.profile."
    fi
}

check_desktop_session() {
    local desktop major minor plasma_output session
    # Staged installs are intentionally usable in CI and build containers. Updates preserve
    # an existing installation and may be run over SSH, where desktop variables are absent.
    [ "$SERVICE" -eq 1 ] || return 0

    desktop="${XDG_CURRENT_DESKTOP:-${DESKTOP_SESSION:-}}"
    session="${XDG_SESSION_TYPE:-}"
    desktop="${desktop,,}"
    session="${session,,}"

    case "$desktop" in
        *gamescope*)
            die "Game Mode is not supported; install from a KDE Plasma desktop session"
            ;;
        *kde*|*plasma*) ;;
        "")
            die "no desktop session detected; install from a terminal inside KDE Plasma"
            ;;
        *)
            die "KDE Plasma is required (detected desktop: ${XDG_CURRENT_DESKTOP:-$DESKTOP_SESSION})"
            ;;
    esac

    [ "$session" = "wayland" ] \
        || die "a KDE Plasma Wayland session is required (detected: ${session:-unknown})"

    command -v plasmashell >/dev/null 2>&1 \
        || die "plasmashell was not found; KDE Plasma 6.2 or newer is required"
    plasma_output=$(plasmashell --version 2>&1) \
        || die "could not determine the KDE Plasma version"
    if [[ "$plasma_output" =~ ([0-9]+)\.([0-9]+) ]]; then
        major=${BASH_REMATCH[1]}
        minor=${BASH_REMATCH[2]}
    else
        die "could not determine the KDE Plasma version from: $plasma_output"
    fi
    if [ "$major" -lt 6 ] || { [ "$major" -eq 6 ] && [ "$minor" -lt 2 ]; }; then
        die "KDE Plasma 6.2 or newer is required (detected: $plasma_output)"
    fi
}

do_install() {
    if [ ! -d "$REPO/src/theater_mode" ]; then
        printf '\033[31merror:\033[0m no theater-mode source found next to this script.\n' >&2
        printf '  install.sh installs the files beside it, so it cannot be run on its own.\n\n' >&2
        printf '  To install, use the bootstrap instead:\n' >&2
        printf '    curl -fsSL https://raw.githubusercontent.com/%s/main/get.sh | bash\n\n' \
            "${PROJECT_REPO}" >&2
        printf '  To uninstall an existing install:\n' >&2
        printf '    theater-mode uninstall\n' >&2
        exit 1
    fi

    resolve_helper dimmer "$DIMMER_OVERRIDE" "$PREBUILT_DIMMER" "$BUILT_DIMMER" \
        DIMMER_SOURCE NEEDS_DIMMER_BUILD
    resolve_helper art "$ART_OVERRIDE" "$PREBUILT_ART" "$BUILT_ART" \
        ART_SOURCE NEEDS_ART_BUILD
    check_desktop_session
    check_prerequisites

    if [ "$NEEDS_DIMMER_BUILD" -eq 1 ]; then
        echo "Building theater-dimmer:"
        make -C "$REPO/src/theater_mode/dimmer"
    fi
    verify_helper dimmer "$DIMMER_SOURCE"

    if [ "$NEEDS_ART_BUILD" -eq 1 ]; then
        echo "Building theater-art:"
        make -C "$REPO/src/theater_mode/art"
    fi
    verify_helper art "$ART_SOURCE"

    echo "Installing theater-mode:"
    place "$DIMMER_SOURCE" "$DIMMER_BIN"
    chmod +x "$DIMMER_BIN"
    place "$ART_SOURCE" "$ART_BIN"
    chmod +x "$ART_BIN"
    place "$REPO/bin/theater-moded" "$DAEMON"
    chmod +x "$DAEMON"
    place "$REPO/bin/theater-mode" "$CLIENT"
    chmod +x "$CLIENT"
    place "$REPO/src/theater_mode" "$APP_DATA/lib/theater_mode"
    prune_package_copy
    place "$REPO/kwin/theater-detect" "$KWIN_SCRIPT"
    render_unit
    place "$REPO/README.md" "$DOC"

    place "$REPO/install.sh" "$SELF_COPY"
    chmod +x "$SELF_COPY"

    local ref_tmp
    mkdir -p "$(dirname "$REF_CONFIG")"
    ref_tmp=$(mktemp "$REF_CONFIG.tmp.XXXXXX") || die "could not stage $REF_CONFIG"
    PYTHONPATH="$REPO/src" python3 -c \
        'import sys
from theater_mode.config import generate_reference_config
sys.stdout.write(generate_reference_config())' > "$ref_tmp" \
        || { rm -f "$ref_tmp"; die "failed to generate $REF_CONFIG"; }
    mv -f "$ref_tmp" "$REF_CONFIG" || { rm -f "$ref_tmp"; die "could not write $REF_CONFIG"; }

    if [ "$SERVICE" -eq 0 ]; then
        cat <<EOF

theater-mode staged successfully (--no-service: nothing was activated).

Files are in place, but neither the service nor the KWin script was turned on:
  systemctl --user daemon-reload
  systemctl --user enable --now theater-mode.service
  kwriteconfig6 --file kwinrc --group Plugins --key ${KWIN_PLUGIN_ID}Enabled true

Reference:     $REF_CONFIG
EOF
        return 0
    fi

    if [ "$SERVICE" -eq 2 ]; then
        # Every file is already in place by now, so the update has succeeded whatever
        # systemd says. Without a running user session (ssh, no lingering) daemon-reload
        # fails; warn and leave the new files, rather than reporting a failed update.
        local was_active=0
        systemctl --user is-active --quiet theater-mode.service && was_active=1
        if ! systemctl --user daemon-reload 2>/dev/null; then
            warn "could not reach the systemd user session; files were updated but the"
            warn "service was not reloaded. Run: systemctl --user daemon-reload"
            warn "                              systemctl --user restart theater-mode.service"
            return 0
        fi
        if [ "$was_active" -eq 1 ]; then
            systemctl --user restart theater-mode.service \
                || warn "files updated, but theater-mode.service did not restart"
        fi
        notify_kwin_reconfigure
        echo
        echo "theater-mode files updated; existing activation state was preserved."
        return 0
    fi

    systemctl --user daemon-reload || die "systemctl --user daemon-reload failed"
    systemctl --user enable theater-mode.service >/dev/null 2>&1 \
        || die "could not enable theater-mode.service via systemctl --user"
    systemctl --user restart theater-mode.service || die "could not start theater-mode.service via systemctl --user"

    if activate_kwin_script; then
        cat <<EOF

theater-mode is installed and running. Nothing else to do.

Cinematic dimming is active with built-in defaults. Launch a game and the screens it
is not on will dim.

Try:
  theater-mode status              Show what the daemon currently sees
  theater-mode config show         Inspect settings and where each one came from
  theater-mode config set effect.dim_factor 0.75

  theater-mode update              Upgrade to the latest release
  theater-mode uninstall           Remove it again

Logs:      journalctl --user -u theater-mode.service -f
Reference: $REF_CONFIG
EOF
    else
        cat <<EOF

theater-mode is installed and running, with one step left.

The KDE configuration tools were not found, so the KWin script could not be enabled
automatically. Turn it on once, by hand:

  System Settings -> Window Management -> KWin Scripts -> "Theater Mode Detector"

Until then the daemon runs but never sees a game start.

Logs:      journalctl --user -u theater-mode.service -f
Reference: $REF_CONFIG
EOF
    fi
}

do_uninstall() {
    echo "The following will be removed:"
    local targets=("$DIMMER_BIN" "$ART_BIN" "$DAEMON" "$CLIENT" "$KWIN_SCRIPT" "$UNIT" "$APP_DATA")
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
    echo "Your settings and caches will not be removed:"
    info "$CONF_DIR/theater-mode/config.toml"
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
    chmod -R u+w "${targets[@]}" 2>/dev/null || true
    rm -rf "${targets[@]}"
    if [ "$SERVICE" -eq 1 ]; then
        systemctl --user daemon-reload || true
    fi

    if [ "$SERVICE" -eq 1 ]; then
        if command -v busctl >/dev/null 2>&1; then
            busctl --user call org.kde.KWin /Scripting org.kde.kwin.Scripting \
                unloadScript s "$KWIN_PLUGIN_ID" >/dev/null 2>&1 || true
        elif command -v qdbus6 >/dev/null 2>&1; then
            qdbus6 org.kde.KWin /Scripting org.kde.kwin.Scripting.unloadScript \
                "$KWIN_PLUGIN_ID" >/dev/null 2>&1 || true
        elif command -v qdbus >/dev/null 2>&1; then
            qdbus org.kde.KWin /Scripting org.kde.kwin.Scripting.unloadScript \
                "$KWIN_PLUGIN_ID" >/dev/null 2>&1 || true
        fi
        deactivate_kwin_script || true
    fi

    echo
    if [ "$SERVICE" -eq 1 ] && command -v kwriteconfig6 >/dev/null 2>&1; then
        echo "Uninstalled."
    else
        echo "Uninstalled. Disable \"Theater Mode Detector\" in System Settings -> KWin Scripts."
    fi
}

ACTION="install"
while [ $# -gt 0 ]; do
    case "$1" in
        -u|--uninstall)  ACTION="uninstall" ;;
        -n|--no-service) SERVICE=0 ;;
        --preserve-service) SERVICE=2 ;;
        -b|--build)      FORCE_BUILD=1 ;;
        --dimmer-bin=*)  DIMMER_OVERRIDE="${1#*=}" ;;
        --dimmer-bin)
            [ $# -ge 2 ] || die "--dimmer-bin requires a file path argument"
            DIMMER_OVERRIDE="$2"; shift ;;
        --art-bin=*)     ART_OVERRIDE="${1#*=}" ;;
        --art-bin)
            [ $# -ge 2 ] || die "--art-bin requires a file path argument"
            ART_OVERRIDE="$2"; shift ;;
        -y|--yes|-f|--force) FORCE=1 ;;
        -h|--help)       show_help; exit 0 ;;
        *)               die "unknown option '$1' (run with --help to see available options)" ;;
    esac
    shift
done

if [ "$ACTION" = "uninstall" ]; then
    do_uninstall
else
    do_install
fi
