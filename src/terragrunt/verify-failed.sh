#!/usr/bin/env bash
# After an apply whose verification failed: a Bug issue with the details, so
# the owner decides. Nothing is rolled back by itself.
#
# Usage: verify-failed.sh <verification.json> <verification.md>
# Env:   GH_TOKEN, GITHUB_REPOSITORY, GITHUB_SHA, GITHUB_EVENT_NAME,
#        GITHUB_SERVER_URL, GITHUB_RUN_ID; OWNER (the organisation variable,
#        assigned to the issue; empty assigns nobody).
set -euo pipefail

JSON=${1:?missing verification.json}
MD=${2:?missing verification.md}
REPO=${GITHUB_REPOSITORY:?}
SHA=${GITHUB_SHA:?}
RUN_URL="$GITHUB_SERVER_URL/$REPO/actions/runs/$GITHUB_RUN_ID"
SHORT=${SHA:0:7}
OWNER=${OWNER:-}
assign_issue=()
if [ -n "$OWNER" ]; then
  assign_issue=(-f "assignees[]=$OWNER")
fi

# The pull request the commit came from, for the title and the links.
pr=$(gh api "repos/$REPO/commits/$SHA/pulls" --jq '[.[] | select(.merged_at != null)][0] // empty' 2>/dev/null || true)
if [ -n "$pr" ]; then
  title=$(jq -r .title <<<"$pr")
  pr_ref="#$(jq -r .number <<<"$pr")"
else
  title=$(gh api "repos/$REPO/commits/$SHA" --jq '.commit.message | split("\n")[0]')
  pr_ref="commit $SHORT"
fi

issue_title="Apply of $SHORT failed its verification"
issue_body=$(mktemp)
cat >"$issue_body" <<EOF
### What happens

The apply of $SHORT ($pr_ref, "$title") went through, but the verification
after it failed.

### What should happen

The second plan proposes nothing, the apply identity has no failed operation
in the Activity Log and every check block passes.

### Where

Run: $RUN_URL

### Impact

The deployed state may not be what the code says. Nothing was rolled back:
decide whether the failure is real, then fix it forward or revert $SHORT by
hand.

### How to reproduce

Run the pipeline on main by hand (scope changed) and read the plan.

### Evidence

$(sed 's/^###/####/' "$MD")

<details><summary>verification.json</summary>

\`\`\`json
$(cat "$JSON")
\`\`\`

</details>
EOF

existing=$(gh issue list --repo "$REPO" --state open --search "\"$issue_title\" in:title" \
  --json number,title --jq ".[] | select(.title == \"$issue_title\") | .number" | head -1)
if [ -n "$existing" ]; then
  gh issue comment "$existing" --repo "$REPO" --body-file "$issue_body"
  echo "Issue #$existing updated."
elif ! gh api -X POST "repos/$REPO/issues" -f title="$issue_title" -F body=@"$issue_body" \
  -f type=Bug "${assign_issue[@]}" -f 'labels[]=prio:high' -f 'labels[]=source:drift' \
  --jq '"Issue: " + .html_url'; then
  # The workflow token may not set the issue type everywhere.
  gh api -X POST "repos/$REPO/issues" -f title="$issue_title" -F body=@"$issue_body" \
    "${assign_issue[@]}" -f 'labels[]=prio:high' -f 'labels[]=source:drift' \
    --jq '"Issue (without type): " + .html_url'
fi
{
  echo "### After the failed verification"
  echo
  echo "- Issue: $issue_title"
} >>"${GITHUB_STEP_SUMMARY:-/dev/null}"
