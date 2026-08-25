#!/usr/bin/env bash
#
# Headless CI runner for GitHub Actions Ubuntu toolchain validation.

set -euo pipefail

CI_IMAGE="theater-mode-runner:ubuntu-24.04"
RELEASE_BUILD_IMAGE="theater-mode-runner:release-build-ubuntu-22.04"
RELEASE_RUNTIME_IMAGE="theater-mode-runner:release-runtime-ubuntu-24.04"
IMAGE_LABEL="com.seanbrar.theater-mode.runner-source"

die()  { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }
note() { printf '\033[34m[runner]\033[0m %s\n' "$*"; }

REPO_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
CI_FILE="$REPO_DIR/tools/runner/Containerfile"
RELEASE_BUILD_FILE="$REPO_DIR/tools/runner/Containerfile.release-build"
RELEASE_RUNTIME_FILE="$REPO_DIR/tools/runner/Containerfile.release-runtime"
cd "$REPO_DIR"

detect_engine() {
    case "$(uname -m)" in
        x86_64|amd64) ;;
        *) die "the hosted release contract supports only x86_64; this host is $(uname -m)" ;;
    esac
    if command -v podman >/dev/null 2>&1; then
        ENGINE="podman"
    elif command -v docker >/dev/null 2>&1; then
        ENGINE="docker"
    else
        die "neither podman nor docker was found; install podman to run the runner"
    fi
}

usage() {
    cat <<'USAGE'
Usage: bin/check-ci [OPTION]
       bin/check-ci -- COMMAND...

Validates the current checkout against the native Ubuntu 24.04 CI job.

Options:
  --quick         run only ./bin/check
  --release [TAG] rehearse release build and runtime-only installation
  --shell         open an interactive shell in the runner
  --rebuild       pull the bases and rebuild every runner image
  -h, --help      show this message

With no option, runs ./bin/check and the install, release, upgrade, and
helper-selection lifecycle. Use -- to run a specific non-interactive command.
USAGE
}

image_fingerprint() {
    sha256sum "$1" | awk '{ print $1 }'
}

image_exists() {
    local image="$1"
    if [ "$ENGINE" = "podman" ]; then
        podman image exists "$image"
    else
        docker image inspect "$image" >/dev/null 2>&1
    fi
}

installed_fingerprint() {
    local image="$1"
    "$ENGINE" image inspect --format "{{ index .Labels \"$IMAGE_LABEL\" }}" \
        "$image" 2>/dev/null
}

build_image() {
    local refresh="$1"
    local image="$2"
    local containerfile="$3"
    local fingerprint
    local build_options=()

    fingerprint="$(image_fingerprint "$containerfile")"
    if [ "$refresh" -eq 1 ]; then
        build_options=(--no-cache)
        if [ "$ENGINE" = "podman" ]; then
            build_options+=(--pull=always)
        else
            build_options+=(--pull)
        fi
    fi
    "$ENGINE" build "${build_options[@]}" \
        --label "$IMAGE_LABEL=$fingerprint" \
        -t "$image" -f "$containerfile" "$REPO_DIR/tools/runner"
}

ensure_image() {
    local image="$1"
    local containerfile="$2"
    local fingerprint

    fingerprint="$(image_fingerprint "$containerfile")"
    if ! image_exists "$image" || [ "$(installed_fingerprint "$image")" != "$fingerprint" ]; then
        note "building runner image ($image)..."
        build_image 0 "$image" "$containerfile"
    fi
}

rebuild_images() {
    detect_engine
    note "rebuilding runner images..."
    build_image 1 "$CI_IMAGE" "$CI_FILE"
    build_image 1 "$RELEASE_BUILD_IMAGE" "$RELEASE_BUILD_FILE"
    build_image 1 "$RELEASE_RUNTIME_IMAGE" "$RELEASE_RUNTIME_FILE"
}

# shellcheck disable=SC2016
run_container() {
    local interactive="$1"
    shift

    detect_engine
    ensure_image "$CI_IMAGE" "$CI_FILE"

    local security_opts=()
    local terminal_opts=()
    if [ "$ENGINE" = "podman" ]; then
        security_opts=(--security-opt label=disable)
    fi
    if [ "$interactive" -eq 1 ]; then
        if [ ! -t 0 ] || [ ! -t 1 ]; then
            die "--shell requires an interactive terminal"
        fi
        terminal_opts=(-it)
    fi

    "$ENGINE" run --rm "${terminal_opts[@]}" "${security_opts[@]}" \
        --tmpfs /tmp:rw,exec,size=2G \
        -v "$REPO_DIR:/source:ro" \
        "$CI_IMAGE" bash -c '
            set -euo pipefail
            workspace=/tmp/workspace
            mkdir "$workspace"
            cp -a /source/. "$workspace/"
            cd "$workspace"
            rm -f src/theater_mode/dimmer/theater-dimmer \
                src/theater_mode/art/theater-art
            git config --global --add safe.directory "$workspace"
            exec "$@"
        ' bash "$@"
}

run_lifecycle() {
    run_container 0 bash -c '
        set -euo pipefail
        ./bin/check
        THEATER_CI_QUIET=1 tools/runner/native-lifecycle.sh all
    '
}

run_logged() {
    local name="$1"
    shift
    local log

    log="$(mktemp)"
    if "$@" >"$log" 2>&1; then
        rm -f "$log"
        note "$name passed"
    else
        cat "$log" >&2
        rm -f "$log"
        die "$name failed"
    fi
}

# shellcheck disable=SC2016
run_release() {
    local tag="${1:-}"
    local volume="theater-mode-release-$$-$RANDOM"
    local security_opts=()

    detect_engine
    ensure_image "$RELEASE_BUILD_IMAGE" "$RELEASE_BUILD_FILE"
    ensure_image "$RELEASE_RUNTIME_IMAGE" "$RELEASE_RUNTIME_FILE"
    if [ "$ENGINE" = "podman" ]; then
        security_opts=(--security-opt label=disable)
    fi

    "$ENGINE" volume create "$volume" >/dev/null
    cleanup_release_volume() {
        "$ENGINE" volume rm "$volume" >/dev/null 2>&1 || true
    }
    trap cleanup_release_volume EXIT

    run_logged "Ubuntu 22.04 release build" \
        "$ENGINE" run --rm "${security_opts[@]}" \
        --tmpfs /tmp:rw,exec,size=2G \
        -v "$REPO_DIR:/source:ro" -v "$volume:/artifacts" \
        "$RELEASE_BUILD_IMAGE" bash --noprofile --norc -e -o pipefail -c '
            workspace=/tmp/workspace
            mkdir "$workspace"
            cp -a /source/. "$workspace/"
            cd "$workspace"
            rm -f src/theater_mode/dimmer/theater-dimmer \
                src/theater_mode/art/theater-art
            git config --global --add safe.directory "$workspace"
            tools/runner/release-build.sh /artifacts "$1"
        ' bash "$tag"

    run_logged "Ubuntu 24.04 runtime verification" \
        "$ENGINE" run --rm "${security_opts[@]}" \
        --tmpfs /tmp:rw,exec,size=2G \
        -v "$volume:/artifacts:ro" \
        "$RELEASE_RUNTIME_IMAGE" \
        bash --noprofile --norc -e -o pipefail \
        /artifacts/verify-release /artifacts

    cleanup_release_volume
    trap - EXIT
    note "release rehearsal passed"
}

case "${1:-}" in
    -h|--help) usage ;;
    --rebuild) rebuild_images ;;
    --shell) run_container 1 bash --noprofile --norc ;;
    --quick) run_container 0 ./bin/check ;;
    --release)
        [ $# -le 2 ] || die "--release accepts at most one tag"
        run_release "${2:-}"
        ;;
    "") run_lifecycle ;;
    --)
        shift
        [ $# -gt 0 ] || die "-- requires a command"
        run_container 0 "$@"
        ;;
    *) die "unknown option: $1" ;;
esac
