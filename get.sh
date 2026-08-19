#!/usr/bin/env bash
#
# One-command installer for theater-mode.
#
#   curl -fsSL https://raw.githubusercontent.com/seanbrar/theater-mode/main/get.sh | bash
#
# Downloads the latest release, verifies its published checksum, and runs its installer.
#
# Arguments after `-s --` are passed straight through to install.sh, e.g.
#
#   curl -fsSL .../get.sh | bash -s -- --no-service
#
set -euo pipefail

REPO="${THEATER_MODE_REPO:-seanbrar/theater-mode}"
API="https://api.github.com/repos/${REPO}/releases/latest"

die()  { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }
info() { printf '  %s\n' "$*"; }

# --------------------------------------------------------------------------

need() {
    command -v "$1" >/dev/null 2>&1 \
        || die "$1 is required but not installed$2"
}

need tar ""
need python3 " (theater-mode is written in Python)"

if command -v curl >/dev/null 2>&1; then
    fetch() { curl -fsSL "$1"; }
elif command -v wget >/dev/null 2>&1; then
    fetch() { wget -qO- "$1"; }
else
    die "neither curl nor wget is installed"
fi

if command -v sha256sum >/dev/null 2>&1; then
    digest() { sha256sum "$1" | awk '{print $1}'; }
elif command -v shasum >/dev/null 2>&1; then
    digest() { shasum -a 256 "$1" | awk '{print $1}'; }
else
    die "neither sha256sum nor shasum is installed; cannot verify the download"
fi

ARCH="$(uname -m)"
case "$(uname -s)" in
    Linux) ;;
    *) die "theater-mode only runs on Linux (this is $(uname -s))" ;;
esac

echo "Looking up the latest theater-mode release..."
META="$(fetch "$API")" || die "could not reach GitHub. Check your network and try again."

# Python is already a runtime requirement, so the bootstrap does not need jq.
PARSED="$(printf '%s' "$META" | python3 -c '
import json, sys
arch = sys.argv[1]
try:
    data = json.load(sys.stdin)
except (json.JSONDecodeError, UnicodeError) as exc:
    raise SystemExit(f"invalid release metadata: {exc}")
if not isinstance(data, dict):
    raise SystemExit("invalid release metadata: expected an object")
version = str(data.get("tag_name", "")).lstrip("v")
filename = f"theater-mode-v{version}-linux-{arch}.tar.gz"
tarball = sha = "-"
assets = data.get("assets", [])
if not isinstance(assets, list):
    raise SystemExit("invalid release metadata: expected an asset list")
def https(value):
    # Only HTTPS, so a tampered metadata response cannot point the download elsewhere.
    return value if isinstance(value, str) and value.startswith("https://") else "-"
for asset in assets:
    if not isinstance(asset, dict):
        continue
    name = asset.get("name", "")
    if name == filename + ".sha256":
        sha = https(asset.get("browser_download_url"))
    elif name == filename:
        tarball = https(asset.get("browser_download_url"))
print(version or "-", tarball, sha)
' "$ARCH")" || die "GitHub returned invalid release metadata. Try again later."
read -r VERSION TARBALL_URL SHA_URL <<< "$PARSED"

[ "$VERSION" != "-" ] || die "no published releases found for $REPO"

if [ "$TARBALL_URL" = "-" ]; then
    printf '\033[31merror:\033[0m release v%s has no build for %s.\n' "$VERSION" "$ARCH" >&2
    printf '  Install from source instead:\n' >&2
    printf '    git clone https://github.com/%s\n' "$REPO" >&2
    printf '    cd theater-mode && ./install.sh --build\n' >&2
    exit 1
fi
[ "$SHA_URL" != "-" ] || die "release v$VERSION publishes no checksum; refusing to install it"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "Downloading theater-mode v$VERSION ($ARCH)..."
fetch "$TARBALL_URL" > "$TMP/release.tar.gz" || die "download failed"

EXPECTED="$(fetch "$SHA_URL" | awk '{print $1}')" || die "could not download the checksum"
if [ "${#EXPECTED}" -ne 64 ] || [[ "$EXPECTED" == *[!0-9a-fA-F]* ]]; then
    die "the published checksum is not a valid SHA-256 digest"
fi
EXPECTED="${EXPECTED,,}"
ACTUAL="$(digest "$TMP/release.tar.gz")"
if [ "$EXPECTED" != "$ACTUAL" ]; then
    printf '\033[31merror:\033[0m the download does not match its published checksum.\n' >&2
    info "expected $EXPECTED" >&2
    info "actual   $ACTUAL" >&2
    die "nothing was installed"
fi
echo "  checksum verified"

mkdir -p "$TMP/tree"
tar xzf "$TMP/release.tar.gz" -C "$TMP/tree" --no-same-owner --no-same-permissions \
    || die "could not extract the release archive"

mapfile -t ROOTS < <(find "$TMP/tree" -mindepth 1 -maxdepth 1 -type d)
[ "${#ROOTS[@]}" -eq 1 ] || die "the release archive has an unexpected layout"
ROOT="${ROOTS[0]}"
[ -f "$ROOT/install.sh" ] || die "the release archive has an unexpected layout"
chmod +x "$ROOT/install.sh"

echo
exec "$ROOT/install.sh" "$@"
