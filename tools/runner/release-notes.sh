#!/usr/bin/env bash
#
# Compose the GitHub release notes for a tag.
#
#   tools/runner/release-notes.sh v0.2.0 > release-notes.md
#
# The installation commands are derived from the tag; the description of what changed is
# read from CHANGELOG.md. A tag with no section there is an error.

set -euo pipefail

die() { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }

repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$repo_dir"

[ $# -eq 1 ] || die "usage: release-notes.sh TAG"
tag="$1"
[[ "$tag" == v* ]] || die "release tag must start with v: $tag"
version="${tag#v}"
[[ "$version" =~ ^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(-(alpha|beta|rc|exp)\.(0|[1-9][0-9]*))?$ ]] \
    || die "unsupported release version: $version"

[ -f CHANGELOG.md ] || die "CHANGELOG.md is missing; the release description is read from it"

project="${GITHUB_REPOSITORY:-seanbrar/theater-mode}"

# A testing build is described by the release it previews, so 0.2.0-beta.1 reads the
# "## 0.2.0" section. That keeps one description per stable release, covering everything
# since the previous stable release rather than only the previous preview.
described="${version%%-*}"

# Only stable releases have a section, so the heading below this one names the release it
# supersedes.
mapfile -t versions < <(awk '/^## [0-9]/ { print $2 }' CHANGELOG.md)
for release in "${versions[@]}"; do
    [[ "$release" =~ ^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]] \
        || die "unsupported release heading in CHANGELOG.md: ## $release"
done
listed="$(printf '%s\n' "${versions[@]}")"
ordered="$(printf '%s\n' "${versions[@]}" | LC_ALL=C sort -Vru)"
[ "$listed" = "$ordered" ] \
    || die "CHANGELOG.md release sections must be newest first, without duplicates"

previous=""
for index in "${!versions[@]}"; do
    if [ "${versions[index]}" = "$described" ]; then
        previous="${versions[index + 1]-}"
        break
    fi
done

section="$(awk -v want="$described" '
    /^## [0-9]/ { found = ($2 == want); next }
    found
' CHANGELOG.md | sed '/./,$!d')"

if [[ -z "${section//[[:space:]]/}" ]]; then
    # The described release's own heading reaches here when its body is empty. It has no
    # tag yet, so the range has to start at an earlier release.
    landmark="${versions[0]-}"
    [ "$landmark" != "$described" ] || landmark="${versions[1]-}"
    hint=""
    [ -z "$landmark" ] || hint="
  What has landed since $landmark:
    git log v$landmark..HEAD --oneline"
    die "$tag needs a \"## $described\" section in CHANGELOG.md.
  Describe what it changes for the people who use it, then tag.$hint"
fi

if [[ "$version" == *-* ]]; then
    cat <<EOF
## This is a testing build

\`theater-mode update\` will not install $version on its own. Ask for it by name:

\`\`\`sh
theater-mode update --release $version
\`\`\`

To install theater-mode for the first time:

\`\`\`sh
curl -fsSL https://raw.githubusercontent.com/$project/main/get.sh |
    bash -s -- --release $version
\`\`\`

When you want to leave this build, \`theater-mode update\` names the current stable release
and the command that returns you to it.
EOF
else
    cat <<EOF
## Install or update

If you already have theater-mode installed:

\`\`\`sh
theater-mode update
\`\`\`

To install theater-mode for the first time:

\`\`\`sh
curl -fsSL https://raw.githubusercontent.com/$project/main/get.sh | bash
\`\`\`
EOF
fi

cat <<EOF

---

## What's changed

$section
EOF

if [ -n "$previous" ]; then
    printf '\n---\n\nEvery commit in this release: https://github.com/%s/compare/v%s...v%s\n' \
        "$project" "$previous" "$version"
fi
