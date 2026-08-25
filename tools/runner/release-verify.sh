#!/usr/bin/env bash

set -euo pipefail

die() { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }

[ $# -eq 1 ] || die "usage: release-verify.sh ARTIFACT_DIR"
artifact_dir="$(cd -- "$1" && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

(cd "$artifact_dir" && sha256sum -c ./*.sha256)
for tool in cc gcc make pkg-config; do
    if command -v "$tool" >/dev/null 2>&1; then
        die "runtime environment unexpectedly provides $tool"
    fi
done

mkdir -p "$work/release" "$work/home"
tar xzf "$artifact_dir"/*.tar.gz -C "$work/release"
root="$(find "$work/release" -maxdepth 1 -mindepth 1 -type d -print -quit)"
[ -n "$root" ] || die "release archive did not contain a root directory"

env -u XDG_DATA_HOME -u XDG_CONFIG_HOME -u XDG_BIN_HOME -u XDG_CACHE_HOME \
    HOME="$work/home" "$root/install.sh" --no-service
for file in .local/bin/theater-mode \
            .local/libexec/theater-mode/theater-moded \
            .local/libexec/theater-mode/theater-dimmer \
            .local/libexec/theater-mode/theater-art \
            .local/share/theater-mode/lib/theater_mode/update.py \
            .local/share/kwin/scripts/theater-detect/metadata.json \
            .config/systemd/user/theater-mode.service; do
    [ -e "$work/home/$file" ] || die "install omitted $file"
done
"$work/home/.local/libexec/theater-mode/theater-dimmer" --version
"$work/home/.local/libexec/theater-mode/theater-art" --version
[ -x "$work/home/.local/share/theater-mode/install.sh" ] \
    || die "installed uninstaller is not executable"

env -u XDG_DATA_HOME -u XDG_CONFIG_HOME -u XDG_BIN_HOME -u XDG_CACHE_HOME \
    HOME="$work/home" "$work/home/.local/share/theater-mode/install.sh" \
    --uninstall --no-service --yes
[ "$(find "$work/home" \( -type f -o -type l \) | wc -l)" -eq 0 ] \
    || die "uninstall left files behind"
