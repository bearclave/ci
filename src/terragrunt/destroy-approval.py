#!/usr/bin/env python3
"""Decides whether the destroys and replaces of a plan are approved.

A plan that destroys or replaces resources is applied only when the owner
approved the pull request after he could read that plan. The plan is the
pull request comment, so "after" means after the plan job ended.

  destroy-approval.py pr <number> <planned-at>
      On a pull request: passes (exit 0) when the pull request has an
      approving review, by a person other than its author, submitted after
      <planned-at> (ISO 8601, UTC) on the head commit, and not superseded by a
      later review of the same person that requests changes or was dismissed.
      Fails (exit 1) otherwise.

  destroy-approval.py main <sha> <workflow>
      On main: writes approved=true|false to $GITHUB_OUTPUT. True when <sha>
      is the merge commit of a pull request whose last run of <workflow>
      (e.g. ci.yml) passed its destroy check BECAUSE the destroys were
      approved (the step STEP below succeeded), not because it had none.

  destroy-approval.py review <head-sha> <workflow>
      On a submitted review: re-runs the failed gate of the last completed
      pull request run of <workflow> for <head-sha>, so that its step STEP
      reads the new review. (Runs from before the check moved into the gate
      have a job "destroy / approved" of its own; that is re-run instead,
      and GitHub re-runs the gate that needs it.)

Reads the API with gh (GH_TOKEN) in GITHUB_REPOSITORY.
"""
import json
import os
import subprocess
import sys

# The step of the gate (on main: of the gate of the merged pull request's
# run) that passes only on an approval.
STEP = "Destroys approved after the plan"
JOB_SUFFIX = "destroy / approved"   # the job of runs before the check moved into the gate
GATE_SUFFIX = "/ gate"


def api(path: str, method: str = "GET"):
    args = ["gh", "api", "-X", method, path]
    if method == "GET":
        args += ["--paginate", "--slurp"]
    r = subprocess.run(args, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"::error::gh api {method} {path}: {r.stderr.strip()}")
    out = r.stdout
    if method != "GET":
        return None
    pages = json.loads(out or "[]")
    # --slurp gives a list of pages; a page is a list or an object holding one.
    items = []
    for page in pages:
        if isinstance(page, list):
            items += page
        else:
            items.append(page)
    return items


def approval(reviews: list, author: str, head_sha: str, planned_at: str):
    """The approving review that counts, or None.

    Only the latest review of each person that is not a plain comment
    counts: an approval followed by a request for changes, or dismissed (a
    push dismisses stale approvals), is no approval.
    """
    latest = {}
    for r in sorted(reviews, key=lambda r: r.get("submitted_at") or ""):
        user = r.get("user") or {}
        if r.get("state") == "COMMENTED" or not r.get("submitted_at"):
            continue
        latest[user.get("login")] = r
    for login, r in latest.items():
        user = r.get("user") or {}
        if (r["state"] == "APPROVED" and login != author and user.get("type") == "User"
                and r.get("commit_id") == head_sha and r["submitted_at"] > planned_at):
            return r
    return None


def last_run(runs: list):
    """The most recent pull request run of the workflow."""
    runs = [r for r in runs if r.get("event") == "pull_request"]
    return max(runs, key=lambda r: r["id"]) if runs else None


def destroy_job(jobs: list):
    """The job that holds the approval check: the gate, or the job of an older run."""
    for job in jobs:
        if any(s.get("name") == STEP for s in job.get("steps", [])) and job.get("name", "").endswith(GATE_SUFFIX):
            return job
    for job in jobs:
        if job.get("name", "").endswith(JOB_SUFFIX):
            return job
    # A gate that skipped the step (no destroys) has it listed too; one without
    # the step list at all (a test, an old API answer) is found by name.
    for job in jobs:
        if job.get("name", "").endswith(GATE_SUFFIX):
            return job
    return None


def approved_by_step(job) -> bool:
    if not job or job.get("conclusion") != "success":
        return False
    return any(s.get("name") == STEP and s.get("conclusion") == "success" for s in job.get("steps", []))


def merged_pull(pulls: list, sha: str):
    for p in pulls:
        if p.get("merged_at") and p.get("merge_commit_sha") == sha:
            return p
    return None


def runs_of(repo: str, workflow: str, sha: str) -> list:
    pages = api(f"repos/{repo}/actions/workflows/{workflow}/runs?head_sha={sha}&event=pull_request&per_page=100")
    return [r for page in pages for r in page.get("workflow_runs", [])]


def jobs_of(repo: str, run_id: int) -> list:
    pages = api(f"repos/{repo}/actions/runs/{run_id}/jobs?filter=latest&per_page=100")
    return [j for page in pages for j in page.get("jobs", [])]


def cmd_pr(repo: str, number: str, planned_at: str) -> int:
    pr = api(f"repos/{repo}/pulls/{number}")[0]
    reviews = api(f"repos/{repo}/pulls/{number}/reviews?per_page=100")
    r = approval(reviews, pr["user"]["login"], pr["head"]["sha"], planned_at)
    if r:
        print(f"Approved by {r['user']['login']} at {r['submitted_at']}, after the plan of {planned_at}.")
        return 0
    print(f"::error::The plan destroys or replaces resources. Read the plan in the pull request "
          f"and approve it: an approval counts only when it comes after the plan ({planned_at}).")
    return 1


def cmd_main(repo: str, sha: str, workflow: str) -> int:
    approved = False
    pr = merged_pull(api(f"repos/{repo}/commits/{sha}/pulls"), sha)
    if not pr:
        print(f"{sha} is not the merge commit of a pull request.")
    else:
        run = last_run(runs_of(repo, workflow, pr["head"]["sha"]))
        job = destroy_job(jobs_of(repo, run["id"])) if run else None
        approved = approved_by_step(job)
        print(f"Pull request #{pr['number']}: the destroys were "
              f"{'approved after its plan' if approved else 'not approved after its plan'}.")
    if out := os.environ.get("GITHUB_OUTPUT"):
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(f"approved={str(approved).lower()}\n")
    return 0


def cmd_review(repo: str, head_sha: str, workflow: str) -> int:
    run = last_run(runs_of(repo, workflow, head_sha))
    if not run:
        print("No pull request run for the head commit yet.")
        return 0
    if run.get("status") != "completed":
        print(f"Run {run['id']} is still {run.get('status')}; its destroy check reads the reviews when it gets there.")
        return 0
    job = destroy_job(jobs_of(repo, run["id"]))
    if not job or job.get("conclusion") != "failure":
        print(f"Nothing to re-run: the destroy check of run {run['id']} is "
              f"{job.get('conclusion') if job else 'missing'}.")
        return 0
    api(f"repos/{repo}/actions/jobs/{job['id']}/rerun", method="POST")
    print(f"Re-running '{job['name']}' of run {run['id']}.")
    return 0


def main(argv: list) -> int:
    repo = os.environ["GITHUB_REPOSITORY"]
    if len(argv) == 3 and argv[0] == "pr":
        return cmd_pr(repo, argv[1], argv[2])
    if len(argv) == 3 and argv[0] == "main":
        return cmd_main(repo, argv[1], argv[2])
    if len(argv) == 3 and argv[0] == "review":
        return cmd_review(repo, argv[1], argv[2])
    sys.exit(__doc__)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
