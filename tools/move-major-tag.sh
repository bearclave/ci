#!/usr/bin/env bash
# Moves the major tag (v2) of bearclave/ci to a published release (v2.x.y).
# Normally major-tag.yml does it as bearclave-bot when the release is
# published; this is the manual fallback and creates a new major tag (v3).
# The only force push an agent may run: the owner allowed this script, not
# force pushes in general (versioning.md).
#
# Usage: tools/move-major-tag.sh v2.3.1
set -euo pipefail

release=${1:?usage: move-major-tag.sh vX.Y.Z}
[[ $release =~ ^v([0-9]+)\.[0-9]+\.[0-9]+$ ]] || { echo "not a release tag: $release" >&2; exit 2; }
major="v${BASH_REMATCH[1]}"

cd "$(git rev-parse --show-toplevel)"
[[ $(git remote get-url origin) =~ github\.com[:/]bearclave/ci(\.git)?$ ]] || { echo "not bearclave/ci" >&2; exit 2; }

git fetch --quiet --force --tags origin
gh release view "$release" --repo bearclave/ci >/dev/null || { echo "no published release $release" >&2; exit 2; }
git verify-tag "$release" >/dev/null 2>&1 || { echo "$release is not a signed tag" >&2; exit 2; }

git tag -fs "$major" -m "$major -> $release" "$release^{}"
git push --force origin "refs/tags/$major"
echo "$major -> $release"
