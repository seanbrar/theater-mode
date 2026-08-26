#!/usr/bin/env bash
#
# One-command installer for theater-mode.
#
#   curl -fsSL https://raw.githubusercontent.com/seanbrar/theater-mode/main/get.sh | bash
#
# Downloads a release, verifies its checksum and, where `gh` is available, its
# build provenance, then runs its installer.
#
# Bootstrap options are consumed here; other arguments are passed through to install.sh.
#
#   curl -fsSL .../get.sh | bash -s -- --no-service
#   curl -fsSL .../get.sh | bash -s -- --release 0.2.0-beta.1
#
set -euo pipefail

REPO="${THEATER_MODE_REPO:-seanbrar/theater-mode}"

die()  { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }
info() { printf '  %s\n' "$*"; }

REQUESTED=""
RELEASE_SET=0
INSTALL_ARGS=()

while [ $# -gt 0 ]; do
    case "$1" in
        --release=*) REQUESTED="${1#*=}"; RELEASE_SET=1 ;;
        --release)
            [ $# -ge 2 ] || die "--release requires a version"
            REQUESTED="$2"
            RELEASE_SET=1
            shift
            ;;
        *) INSTALL_ARGS+=("$1") ;;
    esac
    shift
done

# Preserve the version's original spelling for validation errors.
RELEASE="${REQUESTED#v}"
if [ "$RELEASE_SET" -eq 1 ]; then
    [[ "$RELEASE" =~ ^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(-(alpha|beta|rc|exp)\.(0|[1-9][0-9]*))?$ ]] \
        || die "invalid release version: $REQUESTED"
    API="https://api.github.com/repos/${REPO}/releases/tags/v${RELEASE}"
    API_MISSING="release v$RELEASE was not found on GitHub"
else
    API="https://api.github.com/repos/${REPO}/releases/latest"
    API_MISSING="no stable releases were found for $REPO"
fi

# --------------------------------------------------------------------------

need() {
    command -v "$1" >/dev/null 2>&1 \
        || die "$1 is required but not installed$2"
}

need tar ""
need python3 " (theater-mode is written in Python)"

# Arguments are a URL, a destination path or `-` for stdout, and the message for a 404.
fetch() {
    python3 - "$@" <<'PY'
import http.client
import shutil
import sys
import urllib.error
import urllib.request

url, destination, missing = sys.argv[1], sys.argv[2], sys.argv[3]


def die(message):
    sys.stderr.write(f"\033[31merror:\033[0m {message}\n")
    raise SystemExit(1)


request = urllib.request.Request(url, headers={"User-Agent": "theater-mode-installer"})
try:
    with urllib.request.urlopen(request, timeout=30) as response:
        if destination == "-":
            shutil.copyfileobj(response, sys.stdout.buffer)
        else:
            with open(destination, "wb") as sink:
                shutil.copyfileobj(response, sink)
except urllib.error.HTTPError as exc:
    if exc.code == 404:
        die(missing)
    if exc.code in (403, 429):
        die("GitHub is rate-limiting this address; try again in a few minutes")
    die(f"could not reach GitHub ({exc.code} {exc.reason})")
except urllib.error.URLError as exc:
    die(f"could not reach GitHub: {exc.reason}")
except (http.client.HTTPException, OSError) as exc:
    # A truncated or malformed response raises HTTPException, which is not an OSError.
    die(f"could not download from GitHub: {exc}")
PY
}

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

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

if [ "$RELEASE_SET" -eq 1 ]; then
    echo "Looking up theater-mode v$RELEASE..."
else
    echo "Looking up the latest theater-mode release..."
fi
META="$(fetch "$API" - "$API_MISSING")"

# Python is already a runtime requirement, so the bootstrap does not need jq.
PARSED="$(printf '%s' "$META" | python3 -c '
import json, sys
arch = sys.argv[1]

def die(reason):
    sys.stderr.write(f"\033[31merror:\033[0m GitHub returned {reason}. Try again later.\n")
    raise SystemExit(1)

try:
    data = json.load(sys.stdin)
except (json.JSONDecodeError, UnicodeError) as exc:
    die(f"invalid release metadata: {exc}")
if not isinstance(data, dict):
    die("release metadata that is not an object")
raw_tag = data.get("tag_name")
version = raw_tag.removeprefix("v") if isinstance(raw_tag, str) else ""
if not version:
    die("a release with no version tag")
filename = f"theater-mode-v{version}-linux-{arch}.tar.gz"
tarball = sha = "-"
assets = data.get("assets", [])
if not isinstance(assets, list):
    die("release metadata whose asset list is not a list")
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
fields = [version, tarball, sha]
if any("\n" in value for value in fields):
    # One field per line below, so a value carrying a newline would be read as the next one.
    die("release metadata with a value that spans lines")
print("\n".join(fields))
' "$ARCH")"
{ read -r VERSION; read -r TARBALL_URL; read -r SHA_URL; } <<< "$PARSED"

[ "$RELEASE_SET" -eq 0 ] || [ "$VERSION" = "$RELEASE" ] \
    || die "GitHub returned v$VERSION when v$RELEASE was requested; refusing to install it"

if [ "$TARBALL_URL" = "-" ]; then
    printf '\033[31merror:\033[0m release v%s has no build for %s.\n' "$VERSION" "$ARCH" >&2
    printf '  Install from source instead:\n' >&2
    printf '    git clone https://github.com/%s\n' "$REPO" >&2
    printf '    cd theater-mode && ./install.sh --build\n' >&2
    exit 1
fi
[ "$SHA_URL" != "-" ] \
    || die "release v$VERSION publishes no checksum for $ARCH; refusing to install it"

echo "Downloading theater-mode v$VERSION ($ARCH)..."
fetch "$TARBALL_URL" "$TMP/release.tar.gz" "release v$VERSION has no archive for $ARCH on GitHub"

EXPECTED="$(fetch "$SHA_URL" - "release v$VERSION has no checksum for $ARCH on GitHub" \
    | awk '{print $1}')"
if [ "${#EXPECTED}" -ne 64 ] || [[ "$EXPECTED" == *[!0-9a-fA-F]* ]]; then
    die "the published checksum is not a valid SHA-256 digest"
fi
EXPECTED="${EXPECTED,,}"
ACTUAL="$(digest "$TMP/release.tar.gz")"
if [ "$EXPECTED" != "$ACTUAL" ]; then
    printf '\033[31merror:\033[0m the download does not match its published checksum.\n' >&2
    info "expected $EXPECTED" >&2
    info "actual   $ACTUAL" >&2
    printf '\n  Nothing was installed. An interrupted download is the usual cause, so try\n' >&2
    printf '  again. If it keeps happening, please report it:\n' >&2
    printf '    https://github.com/%s/issues\n' "$REPO" >&2
    exit 1
fi
echo "  checksum verified"

# The attestation is what binds the archive to the release workflow, and a failed
# verification stops the install. Verification is skipped when:
#   - `gh` is absent, unauthenticated, or lacks the `attestation` command
#   - THEATER_MODE_REPO names a fork, which publishes no attestations
#
# --hostname pins the lookup to github.com. A GH_HOST aimed at an enterprise instance
# would otherwise fail an install that should have fallen through to the checksum.
if [ -z "${THEATER_MODE_REPO:-}" ] && command -v gh >/dev/null 2>&1 \
   && gh attestation verify --help >/dev/null 2>&1; then
    rc=0
    gh attestation verify "$TMP/release.tar.gz" --hostname github.com --repo "$REPO" \
        --signer-workflow "$REPO/.github/workflows/release.yml" \
        >/dev/null 2>"$TMP/provenance.err" || rc=$?
    case "$rc" in
        0)
            echo "  build provenance verified"
            ;;
        4)
            ;;
        *)
            printf '\033[31merror:\033[0m could not confirm this download was built by %s.\n' "$REPO" >&2
            info "expected an attestation from $REPO/.github/workflows/release.yml" >&2
            sed 's/^/  /' "$TMP/provenance.err" >&2
            printf '\n  Nothing was installed. If the message above points at GitHub rather than\n' >&2
            printf '  at the archive, try again shortly. Otherwise please report it:\n' >&2
            printf '    https://github.com/%s/issues\n' "$REPO" >&2
            exit 1
            ;;
    esac
fi

mkdir -p "$TMP/tree"
tar xzf "$TMP/release.tar.gz" -C "$TMP/tree" --no-same-owner --no-same-permissions \
    || die "could not extract the release archive"

mapfile -t ROOTS < <(find "$TMP/tree" -mindepth 1 -maxdepth 1 -type d)
[ "${#ROOTS[@]}" -eq 1 ] || die "the release archive has an unexpected layout"
ROOT="${ROOTS[0]}"
[ -f "$ROOT/install.sh" ] || die "the release archive has an unexpected layout"
chmod +x "$ROOT/install.sh"

echo
exec "$ROOT/install.sh" "${INSTALL_ARGS[@]}"
