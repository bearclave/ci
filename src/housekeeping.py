#!/usr/bin/env python3
"""Weekly report of stale branches and pull requests (bearclave/governance
standards/workflow.md#housekeeping), kept in one issue per repository.

- a branch without an open pull request whose last commit is older than
  BRANCH_DAYS (default 30);
- an open pull request without an update for PR_DAYS (default 14); those
  labelled status:blocked are listed apart.

Nothing is deleted or closed except the report issue itself: it is updated in
place while something is stale and closed once nothing is.

One run covers the whole organisation (.github/workflows/housekeeping.yml of
this repository, as the GitHub App bearclave-bot; github_bot.py signs in):
every repository that is not archived and has issues. --repo checks one, with
the GH_TOKEN of the environment; --dry-run writes no issue, it only reports.

Reads OWNER (assigned to the report issue), BRANCH_DAYS, PR_DAYS from the
environment and talks to GitHub through `gh` with GH_TOKEN.

Usage: housekeeping.py --org [--dry-run]       (in the workflow)
       housekeeping.py --repo bearclave/<name> [--dry-run]
       housekeeping.py --self-test
"""
import datetime as dt
import json
import os
import subprocess
import sys

TITLE = "Stale branches and pull requests"
MARKER = "<!-- housekeeping-report -->"
QUERY = """
query($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) {
    defaultBranchRef { name }
    refs(refPrefix: "refs/heads/", first: 100) {
      totalCount
      nodes {
        name
        associatedPullRequests(states: OPEN, first: 1) { totalCount }
        target { ... on Commit { committedDate } }
      }
    }
    pullRequests(states: OPEN, first: 100) {
      totalCount
      nodes {
        number title isDraft updatedAt
        author { login }
        labels(first: 20) { nodes { name } }
      }
    }
  }
}
"""


def when(stamp: str) -> dt.datetime:
    return dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def stale_branches(repo: dict, now: dt.datetime, days: int) -> list:
    """(name, age in days) of branches without an open pull request."""
    default = (repo.get("defaultBranchRef") or {}).get("name", "main")
    out = []
    for ref in repo["refs"]["nodes"]:
        date = (ref.get("target") or {}).get("committedDate")
        if ref["name"] == default or ref["associatedPullRequests"]["totalCount"] or not date:
            continue
        age = (now - when(date)).days
        if age > days:
            out.append((ref["name"], age))
    return sorted(out, key=lambda x: -x[1])


def stale_prs(repo: dict, now: dt.datetime, days: int) -> tuple:
    """(stale, blocked): lists of (number, title, draft, age in days)."""
    stale, blocked = [], []
    for pr in repo["pullRequests"]["nodes"]:
        age = (now - when(pr["updatedAt"])).days
        if age <= days:
            continue
        row = (pr["number"], pr["title"], pr["isDraft"], age)
        labels = {x["name"] for x in pr["labels"]["nodes"]}
        (blocked if "status:blocked" in labels else stale).append(row)
    key = lambda x: -x[3]  # noqa: E731
    return sorted(stale, key=key), sorted(blocked, key=key)


def report(branches: list, prs: list, blocked: list, branch_days: int, pr_days: int, truncated: bool) -> str:
    lines = [MARKER, "## What is wrong", "",
             "Branches or pull requests have been left lying "
             f"(branches without a pull request over {branch_days} days, pull requests "
             f"without an update over {pr_days} days).", ""]
    if branches:
        lines += ["### Branches without a pull request", "", "| Branch | Last commit |", "|---|---|"]
        lines += [f"| `{name}` | {age} days ago |" for name, age in branches]
        lines += [""]
    for title, rows in (("Pull requests without an update", prs), ("Blocked pull requests", blocked)):
        if rows:
            lines += [f"### {title}", "", "| Pull request | Draft | Last update |", "|---|---|---|"]
            lines += [f"| #{n} {t} | {'yes' if d else 'no'} | {age} days ago |" for n, t, d, age in rows]
            lines += [""]
    if truncated:
        lines += ["Only the first 100 branches and pull requests were read.", ""]
    lines += ["## Why it matters", "",
              "A forgotten branch holds work nobody sees; a stale pull request drifts from `main`.", "",
              "## Proposed fix", "",
              "Per item: open a draft pull request for the branch or delete it; finish, block "
              "(`status:blocked` with a comment why) or close the pull request. Nothing is deleted "
              "automatically. This issue is updated every week and closed when nothing is stale.", "",
              "## Found in", "", "The weekly housekeeping run of the organisation (`housekeeping.yml` of `bearclave/ci`)."]
    return "\n".join(lines) + "\n"


def gh(*args, check=True) -> subprocess.CompletedProcess:
    r = subprocess.run(["gh", *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args[:3])}: {r.stderr.strip()}")
    return r


def find_issue(repo: str) -> str:
    out = gh("issue", "list", "--repo", repo, "--state", "open", "--search", f'in:title "{TITLE}"',
             "--json", "number,body").stdout
    for issue in json.loads(out or "[]"):
        if MARKER in (issue.get("body") or ""):
            return str(issue["number"])
    return ""


def check_repo(repo: str, branch_days: int, pr_days: int, assignee: str, dry_run: bool = False) -> list:
    """Updates the report issue of one repository; returns the summary lines."""
    owner, name = repo.split("/")
    data = json.loads(gh("api", "graphql", "-f", f"query={QUERY}", "-F", f"owner={owner}",
                         "-F", f"name={name}").stdout)["data"]["repository"]
    now = dt.datetime.now(dt.timezone.utc)
    branches = stale_branches(data, now, branch_days)
    prs, blocked = stale_prs(data, now, pr_days)
    truncated = data["refs"]["totalCount"] > 100 or data["pullRequests"]["totalCount"] > 100
    number = find_issue(repo)
    summary = [f"- Stale branches: {len(branches)}", f"- Stale pull requests: {len(prs)}",
               f"- Blocked pull requests over {pr_days} days: {len(blocked)}"]

    if dry_run:
        summary.append(f"- Dry run: {'would update #' + number if number else 'would open an issue'}"
                       if branches or prs or blocked else "- Nothing is stale.")
    elif not branches and not prs and not blocked:
        if number:
            gh("issue", "close", number, "--repo", repo, "--reason", "completed",
               "--comment", "Nothing is stale any more.")
            summary.append(f"- Closed #{number}: nothing is stale.")
        else:
            summary.append("- Nothing is stale.")
    else:
        body = report(branches, prs, blocked, branch_days, pr_days, truncated)
        path = os.path.join(os.environ.get("RUNNER_TEMP", "."), "housekeeping.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(body)
        if number:
            gh("issue", "edit", number, "--repo", repo, "--body-file", path)
            summary.append(f"- Updated #{number}.")
        else:
            args = ["api", "-X", "POST", f"repos/{repo}/issues", "-f", f"title={TITLE}",
                    "-F", f"body=@{path}"] + (["-f", f"assignees[]={assignee}"] if assignee else [])
            # The issue type may not be settable with this token; the issue
            # matters more than its type.
            r = gh(*args, "-f", "type=Debt", check=False)
            if r.returncode != 0:
                print(f"::warning::Created without the type Debt: {r.stderr.strip()}")
                r = gh(*args)
            summary.append(f"- Opened #{json.loads(r.stdout)['number']}.")
    return summary


def org_repositories() -> list:
    """bearclave/<name> of every repository that is not archived and has issues."""
    out = gh("api", "--paginate", "orgs/bearclave/repos?per_page=100",
             "--jq", ".[] | select((.archived | not) and .has_issues) | .full_name").stdout
    return sorted(line for line in out.splitlines() if line)


def bot_token(write: bool) -> str:
    """An installation token of bearclave-bot, limited to what the report needs."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import github_bot  # noqa: E402 - only the workflow signs in
    return github_bot.Bot().token({"contents": "read", "issues": "write" if write else "read",
                                   "pull_requests": "read", "metadata": "read"})


def say(text: str) -> None:
    print(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(text + "\n")


def main(argv: list) -> int:
    branch_days = int(os.environ.get("BRANCH_DAYS") or 30)
    pr_days = int(os.environ.get("PR_DAYS") or 14)
    assignee = os.environ.get("OWNER", "").strip()
    dry_run = "--dry-run" in argv
    if "--repo" in argv:
        repos = [argv[argv.index("--repo") + 1]]
    elif "--org" in argv:
        os.environ["GH_TOKEN"] = bot_token(write=not dry_run)
        repos = org_repositories()
    else:
        sys.exit(__doc__)
    failed = []
    say(f"## Housekeeping{' (dry run)' if dry_run else ''}")
    for repo in repos:
        try:
            lines = check_repo(repo, branch_days, pr_days, assignee, dry_run)
        except (RuntimeError, KeyError, ValueError) as e:
            failed.append(repo)
            print(f"::warning::{repo}: {e}")
            continue
        say(f"\n### {repo}\n\n" + "\n".join(lines))
    if failed:
        say(f"\nNot checked: {', '.join(failed)}.")
    # One repository that cannot be read does not stop the others, but the
    # run turns red so that it is noticed.
    return 1 if failed else 0


def self_test() -> int:
    now = when("2026-11-19T00:00:00Z")
    repo = {
        "defaultBranchRef": {"name": "main"},
        "refs": {"totalCount": 3, "nodes": [
            {"name": "main", "associatedPullRequests": {"totalCount": 0}, "target": {"committedDate": "2026-01-01T00:00:00Z"}},
            {"name": "feat/old", "associatedPullRequests": {"totalCount": 0}, "target": {"committedDate": "2026-10-01T00:00:00Z"}},
            {"name": "feat/open", "associatedPullRequests": {"totalCount": 1}, "target": {"committedDate": "2026-01-01T00:00:00Z"}},
        ]},
        "pullRequests": {"totalCount": 2, "nodes": [
            {"number": 4, "title": "feat: a", "isDraft": True, "updatedAt": "2026-11-01T00:00:00Z", "labels": {"nodes": []}},
            {"number": 5, "title": "feat: b", "isDraft": False, "updatedAt": "2026-11-18T00:00:00Z", "labels": {"nodes": []}},
        ]},
    }
    assert stale_branches(repo, now, 30) == [("feat/old", 49)]
    assert stale_prs(repo, now, 14) == ([(4, "feat: a", True, 18)], [])
    body = report([("feat/old", 49)], [(4, "feat: a", True, 18)], [], 30, 14, False)
    assert body.startswith(MARKER) and "| `feat/old` | 49 days ago |" in body
    print("self-test ok")
    return 0


if __name__ == "__main__":
    sys.exit(self_test() if "--self-test" in sys.argv else main(sys.argv[1:]))
