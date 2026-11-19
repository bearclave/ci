#!/usr/bin/env bash
# After an apply whose verification failed: a Bug issue with the details and
# a draft pull request that reverts the commit, so the owner decides. Nothing
# is rolled back by itself.
#
# Usage: verify-failed.sh <verification.json> <verification.md>
# Env:   GH_TOKEN, GITHUB_REPOSITORY, GITHUB_SHA, GITHUB_EVENT_NAME,
#        GITHUB_SERVER_URL, GITHUB_RUN_ID; run in a checkout of main with
#        its whole history.
set -euo pipefail

JSON=${1:?missing verification.json}
MD=${2:?missing verification.md}
REPO=${GITHUB_REPOSITORY:?}
SHA=${GITHUB_SHA:?}
RUN_URL="$GITHUB_SERVER_URL/$REPO/actions/runs/$GITHUB_RUN_ID"
SHORT=${SHA:0:7}
OWNER=matej-hrabalek

# The pull request the commit came from, for the title and the links.
pr=$(gh api "repos/$REPO/commits/$SHA/pulls" --jq '[.[] | select(.merged_at != null)][0] // empty' 2>/dev/null || true)
if [ -n "$pr" ]; then
  title=$(jq -r .title <<<"$pr")
  pr_ref="#$(jq -r .number <<<"$pr")"
else
  title=$(git log -1 --format=%s "$SHA")
  pr_ref="commit $SHORT"
fi

# The revert: only for a push, where the run belongs to one commit. A manual
# run applies whatever main holds, so there is nothing single to revert.
revert_note=""
branch="revert/$SHORT"
# The token goes into each git command, never into .git/config.
auth=$(printf 'x-access-token:%s' "$GH_TOKEN" | base64 -w0)
git_auth() { git -c http.extraheader="AUTHORIZATION: basic $auth" "$@"; }
if [ "$GITHUB_EVENT_NAME" != "push" ]; then
  revert_note="No revert pull request: the run was started by hand and applies the whole of main, not one commit."
elif git_auth ls-remote --exit-code --heads origin "$branch" >/dev/null 2>&1; then
  revert_note="The branch \`$branch\` exists already; its pull request is the revert."
else
  git config user.name "github-actions[bot]"
  git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
  git switch -c "$branch" origin/main
  # A squash merge has one parent; a merge commit needs the mainline.
  mainline=()
  [ "$(git rev-list --parents -n1 "$SHA" | wc -w)" -gt 2 ] && mainline=(-m1)
  rtitle="revert: $title"
  [ "${#rtitle}" -le 72 ] || rtitle="${rtitle:0:69}..."
  if ! git revert --no-commit "${mainline[@]}" "$SHA"; then
    git revert --abort || true
    revert_note="The revert of \`$SHORT\` does not apply cleanly on main; revert it by hand."
  else
    git commit -q -F - <<EOF
$rtitle

This reverts commit $SHA ($pr_ref): the verification after its apply
failed in $RUN_URL. Merge only if the owner decides to roll back.
EOF
    if ! git_auth push -q origin "HEAD:refs/heads/$branch"; then
      revert_note="The revert branch could not be pushed (a workflow file in the commit needs a token with the workflows permission); revert $SHORT by hand."
    else
      body=$(mktemp)
      cat >"$body" <<EOF
## Why

The verification after the apply of $SHORT ($pr_ref) failed: $RUN_URL

## What changes

Reverts $SHA.

## Impact

Merging applies the previous code again. Decide first whether the failure is
real; nothing is rolled back by itself.

## Verification

$(sed 's/^###/####/' "$MD")

## Review

Opened by the pipeline as a draft. Pushes made with the workflow token start
no checks: push a commit or close and reopen the pull request to run them.

## Links

- Run: $RUN_URL
- Original: $pr_ref
EOF
      if url=$(gh pr create --repo "$REPO" --draft --base main --head "$branch" \
        --title "$rtitle" --body-file "$body" --assignee "$OWNER" 2>&1); then
        revert_note="Draft revert pull request: $url"
      else
        echo "::warning::gh pr create: $url"
        revert_note="The branch \`$branch\` holds the revert; open the draft pull request from it: $GITHUB_SERVER_URL/$REPO/compare/main...$branch?expand=1 (GitHub Actions may not create pull requests in this repository)."
      fi
    fi
  fi
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

The deployed state may not be what the code says. $revert_note

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
  -f type=Bug -f "assignees[]=$OWNER" -f 'labels[]=prio:high' -f 'labels[]=source:drift' \
  --jq '"Issue: " + .html_url'; then
  # The workflow token may not set the issue type everywhere.
  gh api -X POST "repos/$REPO/issues" -f title="$issue_title" -F body=@"$issue_body" \
    -f "assignees[]=$OWNER" -f 'labels[]=prio:high' -f 'labels[]=source:drift' \
    --jq '"Issue (without type): " + .html_url'
fi
echo "$revert_note"
{
  echo "### After the failed verification"
  echo
  echo "- Issue: $issue_title"
  echo "- $revert_note"
} >>"${GITHUB_STEP_SUMMARY:-/dev/null}"
