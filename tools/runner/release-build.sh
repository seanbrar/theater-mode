#!/usr/bin/env bash

set -euo pipefail

die() { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }

repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$repo_dir"

[ $# -ge 1 ] || die "usage: release-build.sh OUTPUT_DIR [TAG]"
out_dir="$1"
tag="${2:-}"
source_version="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' src/theater_mode/__init__.py)"
[ -n "$source_version" ] || die "could not read the source version"
[[ "$source_version" =~ ^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(-(alpha|beta|rc|exp)\.(0|[1-9][0-9]*))?$ ]] \
    || die "unsupported release version: $source_version"
[ -n "$tag" ] || tag="v$source_version"
[ "${tag#v}" = "$source_version" ] \
    || die "tag $tag does not match __version__ $source_version"

ldd --version | sed -n 1p
pkg-config --modversion wayland-client

make -C src/theater_mode/dimmer
./bin/check-abi-floor src/theater_mode/dimmer/theater-dimmer 2.35
make -C src/theater_mode/art
./bin/check-abi-floor src/theater_mode/art/theater-art 2.35 "libc.so.6 libm.so.6"

mkdir -p "$out_dir"
./bin/make-release \
    --dimmer-bin src/theater_mode/dimmer/theater-dimmer \
    --art-bin src/theater_mode/art/theater-art \
    --outdir "$out_dir"
install -m 755 tools/runner/release-verify.sh "$out_dir/verify-release"
