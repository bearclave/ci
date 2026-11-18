#!/usr/bin/env bash
# Put the plan summary into the pull request as ONE comment. The comment is
# found by the marker on its first line and rewritten, so a pull request with
# ten pushes does not collect ten walls of stale numbers.
#
# Usage: pr-comment.sh <pr-number> <markdown-file> <marker>
set -euo pipefail

PR=${1:?missing pull request number}
FILE=${2:?missing markdown file}
MARKER=${3:?missing marker}
REPO=${GITHUB_REPOSITORY:?missing GITHUB_REPOSITORY}

BODY=$(printf '<!-- %s -->\n%s\n' "$MARKER" "$(cat "$FILE")")

ID=$(gh api --paginate "/repos/$REPO/issues/$PR/comments" \
  --jq "map(select(.body | startswith(\"<!-- $MARKER -->\"))) | .[0].id // empty")

if [ -n "$ID" ]; then
  gh api -X PATCH "/repos/$REPO/issues/comments/$ID" -f body="$BODY" >/dev/null
  echo "comment $ID rewritten"
else
  gh api -X POST "/repos/$REPO/issues/$PR/comments" -f body="$BODY" >/dev/null
  echo "comment created"
fi
