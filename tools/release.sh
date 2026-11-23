#!/usr/bin/env bash
set -euo pipefail
# Tags and publishes the next release of a bearclave repository, by the owner
# (versioning.md#releasing); an agent never runs it. Usage in the README.

usage() {
  cat <<'EOF'
Usage: tools/release.sh <repo-path> [vX.Y.Z] [--dry-run] [--force-open] [--force-version]

  <repo-path>      a clone of the repository to release, on main
  vX.Y.Z           the version you expect; refused when it differs from the computed one
  --dry-run        every check and the computed version, then stop before tagging
  --force-open     release although an open pull request may be about to merge
  --force-version  release vX.Y.Z although it differs from the computed version
EOF
}

die() { echo "release: $*" >&2; exit 1; }

repo="" wanted="" dry_run=false force_open=false force_version=false
for arg in "$@"; do
  case "$arg" in
    --dry-run) dry_run=true ;;
    --force-open) force_open=true ;;
    --force-version) force_version=true ;;
    -h | --help) usage; exit 0 ;;
    -*) usage >&2; exit 2 ;;
    v[0-9]*) wanted=$arg ;;
    *) [ -z "$repo" ] || { usage >&2; exit 2; }; repo=$arg ;;
  esac
done
[ -n "$repo" ] || { usage >&2; exit 2; }
if [ -n "$wanted" ] && ! [[ $wanted =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  die "$wanted is not vX.Y.Z"
fi
$force_version && [ -z "$wanted" ] && die "--force-version needs the version"

ci=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo"
cd "$(git rev-parse --show-toplevel)"
slug=$(gh repo view --json nameWithOwner --jq .nameWithOwner)
[[ $slug == bearclave/* ]] || die "$slug is not a bearclave repository"

# (a) The tag goes on what is on GitHub's main, and nothing else is in the clone.
git fetch --quiet --tags --force origin main
branch=$(git symbolic-ref --quiet --short HEAD || true)
[ "$branch" = main ] || die "the checkout is on '${branch:-a detached HEAD}', not main"
[ "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" ] ||
  die "main is not origin/main: git pull --ff-only (or drop the local commits)"
[ -z "$(git status --porcelain)" ] || die "the checkout is not clean (git status)"
head=$(git rev-parse --short HEAD)

# (b) A pull request with auto-merge on, or approved and mergeable, may land
# right after the tag: that is how an empty release happens.
landing=$(gh pr list --repo "$slug" --state open --base main --limit 100 \
  --json number,title,isDraft,autoMergeRequest,reviewDecision,mergeable \
  --jq '.[] | select(.autoMergeRequest != null or (.isDraft == false and .reviewDecision == "APPROVED" and .mergeable != "CONFLICTING"))
        | "  #\(.number) \(.title) (\(if .autoMergeRequest != null then "auto-merge" else "approved" end))"')
if [ -n "$landing" ]; then
  echo "Open pull requests that may be about to merge:"
  echo "$landing"
  $force_open || die "wait for them to merge (or turn auto-merge off), or pass --force-open"
fi

# (c) The version from the titles since the last tag, by src/release_version.py
# (the rule of versioning.md, the same as release.yml).
out=$(mktemp)
trap 'rm -f "$out"' EXIT
GITHUB_OUTPUT=$out python3 "$ci/src/release_version.py" >/dev/null
previous=$(sed -n 's/^previous=//p' "$out")
computed=$(sed -n 's/^version=//p' "$out")
bump=$(sed -n 's/^bump=//p' "$out")

echo "Repository:   $slug at $head (main)"
echo "Last tag:     ${previous:-none}"
echo "Merged since:"
if [ -n "$previous" ]; then
  git log --first-parent --format='  %s' "$previous..HEAD"
else
  git log --first-parent --format='  %s' HEAD
fi
echo "Bump:         $bump"
echo "Next version: ${computed:-none}"

version=${wanted:-$computed}
if [ -z "$computed" ] && ! $force_version; then
  die "nothing since ${previous:-the start} calls for a release (docs, test, ci, chore only)"
fi
if [ -n "$wanted" ] && [ "$wanted" != "$computed" ]; then
  $force_version || die "you asked for $wanted, the titles give ${computed:-no release}; pass --force-version to tag $wanted"
  if [ -n "$previous" ] && [ "$(printf '%s\n%s\n' "$previous" "$wanted" | sort -V | tail -n1)" != "$wanted" ] ||
    [ "$wanted" = "$previous" ]; then
    die "$wanted is not above the last tag $previous"
  fi
fi
if git rev-parse --quiet --verify "refs/tags/$version" >/dev/null; then
  die "the tag $version exists already"
fi

if $dry_run; then
  echo "Dry run: would tag $version on $head, push it and publish the release of $slug."
  exit 0
fi

# (e) Typed confirmation, then the signed tag and the release.
read -r -p "Type $version to tag $head and publish the release of $slug: " answer
[ "$answer" = "$version" ] || die "not confirmed, nothing tagged"
git tag -s "$version" -m "$version"
git push origin "refs/tags/$version"
gh release create "$version" --repo "$slug" --verify-tag --generate-notes
