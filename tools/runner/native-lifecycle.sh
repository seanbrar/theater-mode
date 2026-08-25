#!/usr/bin/env bash

set -euo pipefail

fail() { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }

repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
run_root="${RUNNER_TEMP:-/tmp/theater-mode-native-lifecycle}"
mkdir -p "$run_root"
cd "$repo_dir"

source_install() {
    local fake="$run_root/source-install"
    local bin="$fake/bin & tools"
    local unit
    local ambient="$run_root/ambient/theater_mode"

    mkdir -p "$fake"
    env -u XDG_DATA_HOME -u XDG_CONFIG_HOME -u XDG_BIN_HOME -u XDG_CACHE_HOME \
        HOME="$fake" XDG_BIN_HOME="$bin" ./install.sh --no-service

    for file in "$bin/theater-mode" \
                "$fake/.local/libexec/theater-mode/theater-moded" \
                "$fake/.local/libexec/theater-mode/theater-dimmer" \
                "$fake/.local/libexec/theater-mode/theater-art" \
                "$fake/.local/share/theater-mode/lib/theater_mode/__init__.py" \
                "$fake/.local/share/theater-mode/config.reference.toml" \
                "$fake/.local/share/kwin/scripts/theater-detect/metadata.json" \
                "$fake/.config/systemd/user/theater-mode.service"; do
        [ -e "$file" ] || fail "install omitted $file"
    done

    unit="$fake/.config/systemd/user/theater-mode.service"
    [ "$(sed -n 's|^ExecStart=/usr/bin/env -- "\(.*\)"$|\1|p' "$unit")" = \
        "$fake/.local/libexec/theater-mode/theater-moded" ] \
        || fail "unit contains the wrong daemon path"
    systemd-analyze verify "$unit"

    [ ! -e "$fake/.local/share/theater-mode/lib/theater_mode/dimmer" ] \
        || fail "dimmer sources leaked into the install"
    [ ! -e "$fake/.local/share/theater-mode/lib/theater_mode/art" ] \
        || fail "art sources leaked into the install"
    [ -z "$(find "$fake/.local/share/theater-mode/lib" -name __pycache__ -print -quit)" ] \
        || fail "bytecode leaked into the install"

    mkdir -p "$ambient"
    printf '%s\n' '# Deliberately incomplete ambient package.' > "$ambient/__init__.py"
    (cd / && env -i PATH=/usr/bin:/bin HOME="$fake" \
        PYTHONPATH="$run_root/ambient" "$bin/theater-mode" --help >/dev/null) \
        || fail "installed client preferred an ambient Python package"

    env -u XDG_DATA_HOME -u XDG_CONFIG_HOME -u XDG_BIN_HOME -u XDG_CACHE_HOME \
        HOME="$fake" XDG_BIN_HOME="$bin" \
        "$fake/.local/share/theater-mode/install.sh" --uninstall --no-service --yes
    [ "$(find "$fake" \( -type f -o -type l \) | wc -l)" -eq 0 ] \
        || fail "uninstall left files behind"
}

release_archive() {
    local out="$run_root/dist"
    local fake="$run_root/release-home"
    local root

    mkdir -p "$out" "$fake"
    ./bin/make-release \
        --dimmer-bin src/theater_mode/dimmer/theater-dimmer \
        --art-bin src/theater_mode/art/theater-art --outdir "$out"
    tar xzf "$out"/*.tar.gz -C "$run_root"
    root="$(find "$run_root" -maxdepth 1 -type d -name 'theater-mode-v*' -print -quit)"
    [ -n "$root" ] || fail "release archive did not contain a root directory"

    env -u XDG_DATA_HOME -u XDG_CONFIG_HOME -u XDG_BIN_HOME -u XDG_CACHE_HOME \
        HOME="$fake" "$root/install.sh" --no-service
    [ -f "$fake/.local/share/theater-mode/lib/theater_mode/update.py" ] \
        || fail "release install omitted update.py"
    "$fake/.local/libexec/theater-mode/theater-dimmer" --version
    "$fake/.local/libexec/theater-mode/theater-art" --version
    env -u XDG_DATA_HOME -u XDG_CONFIG_HOME -u XDG_BIN_HOME -u XDG_CACHE_HOME \
        HOME="$fake" "$fake/.local/share/theater-mode/install.sh" \
        --uninstall --no-service --yes
    [ "$(find "$fake" \( -type f -o -type l \) | wc -l)" -eq 0 ] \
        || fail "release uninstall left files behind"
}

upgrade_install() {
    local fake="$run_root/upgrade-home"
    local root

    root="$(find "$run_root" -maxdepth 1 -type d -name 'theater-mode-v*' -print -quit)"
    [ -n "$root" ] || fail "upgrade stage requires the release archive stage"
    run_upgrade() {
        env -u XDG_DATA_HOME -u XDG_CONFIG_HOME -u XDG_BIN_HOME -u XDG_CACHE_HOME \
            HOME="$fake" "$@"
    }

    run_upgrade "$root/install.sh" --no-service
    # Seed files that only a replacement install can repair.
    echo 'stale' > "$fake/.local/libexec/theater-mode/theater-dimmer"
    printf 'stale\n' >> "$fake/.local/share/theater-mode/lib/theater_mode/__init__.py"
    run_upgrade "$root/install.sh" --preserve-service

    run_upgrade "$fake/.local/libexec/theater-mode/theater-dimmer" --version >/dev/null \
        || fail "upgrade left an unusable theater-dimmer"
    run_upgrade "$fake/.local/libexec/theater-mode/theater-art" --version >/dev/null \
        || fail "upgrade left an unusable theater-art"
    if grep -q '^stale$' "$fake/.local/share/theater-mode/lib/theater_mode/__init__.py"; then
        fail "upgrade did not replace the installed package"
    fi
    for file in "$fake/.local/bin/theater-mode" \
                "$fake/.local/libexec/theater-mode/theater-moded" \
                "$fake/.local/libexec/theater-mode/theater-art" \
                "$fake/.local/libexec/theater-mode/theater-dimmer" \
                "$fake/.local/share/theater-mode/install.sh" \
                "$fake/.config/systemd/user/theater-mode.service"; do
        [ -e "$file" ] || fail "upgrade lost $file"
    done
    systemd-analyze verify "$fake/.config/systemd/user/theater-mode.service"

    run_upgrade "$fake/.local/share/theater-mode/install.sh" --uninstall --no-service --yes
    [ "$(find "$fake" \( -type f -o -type l \) | wc -l)" -eq 0 ] \
        || fail "uninstall after upgrade left files behind"
}

helper_selection() {
    local root
    local built="$run_root/build-home"
    local partial="$run_root/art-build-release"
    local art_built="$run_root/art-build-home"
    local given="$run_root/given-home"
    local bad="$run_root/bad-helper"

    root="$(find "$run_root" -maxdepth 1 -type d -name 'theater-mode-v*' -print -quit)"
    [ -n "$root" ] || fail "helper stage requires the release archive stage"
    run_override() {
        env -u XDG_DATA_HOME -u XDG_CONFIG_HOME -u XDG_BIN_HOME -u XDG_CACHE_HOME \
            HOME="$1" "${@:2}"
    }

    # --build ignores both prebuilt helpers.
    run_override "$built" "$root/install.sh" --no-service --build
    run_override "$built" "$built/.local/libexec/theater-mode/theater-dimmer" --version >/dev/null \
        || fail "--build produced an unusable dimmer helper"
    run_override "$built" "$built/.local/libexec/theater-mode/theater-art" --version >/dev/null \
        || fail "--build produced an unusable art helper"

    # Each helper resolves independently. Missing art must not rebuild an explicit dimmer.
    cp -a "$root" "$partial"
    rm -f "$partial/bin/theater-art"
    rm -rf "$partial/src/theater_mode/dimmer"
    run_override "$art_built" "$partial/install.sh" --no-service \
        --dimmer-bin "$root/bin/theater-dimmer"
    run_override "$art_built" "$art_built/.local/libexec/theater-mode/theater-art" --version >/dev/null \
        || fail "art-only build produced an unusable helper"

    # Explicit helper paths win over packaged sources.
    run_override "$given" "$root/install.sh" --no-service \
        --dimmer-bin src/theater_mode/dimmer/theater-dimmer \
        --art-bin src/theater_mode/art/theater-art
    cmp -s src/theater_mode/dimmer/theater-dimmer \
        "$given/.local/libexec/theater-mode/theater-dimmer" \
        || fail "--dimmer-bin did not install the helper it was given"
    cmp -s src/theater_mode/art/theater-art \
        "$given/.local/libexec/theater-mode/theater-art" \
        || fail "--art-bin did not install the helper it was given"

    # Helper verification runs before installation starts.
    printf '#!/nonexistent\n' > "$bad"
    chmod +x "$bad"
    if run_override "$run_root/bad-home" "$root/install.sh" --no-service \
         --dimmer-bin "$bad" 2>/dev/null; then
        fail "installer accepted a dimmer helper that cannot run"
    fi
    if run_override "$run_root/bad-art-home" "$root/install.sh" --no-service \
         --art-bin "$bad" 2>/dev/null; then
        fail "installer accepted an art helper that cannot run"
    fi
}

run_stage() {
    local name="$1"
    local function="$2"
    local log="$run_root/$function.log"

    if [ "${THEATER_CI_QUIET:-0}" -ne 1 ]; then
        "$function"
        return
    fi
    if "$function" >"$log" 2>&1; then
        printf '[runner] %s passed.\n' "$name"
    else
        cat "$log" >&2
        fail "$name failed"
    fi
}

case "${1:-}" in
    source) source_install ;;
    release) release_archive ;;
    upgrade) upgrade_install ;;
    helpers) helper_selection ;;
    all)
        run_stage "Source install lifecycle" source_install
        run_stage "Release archive lifecycle" release_archive
        run_stage "In-place upgrade path" upgrade_install
        run_stage "Helper build and override selection" helper_selection
        ;;
    *) fail "usage: native-lifecycle.sh {source|release|upgrade|helpers|all}" ;;
esac
