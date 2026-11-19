#!/usr/bin/env python3
"""The commit a run on main compares against: the head of the last run whose apply went through.

Usage: base-commit.py <repository> <workflow file>

Prints the head commit of the newest run on main (a push or a manual run)
that either

- succeeded as a whole: its apply went through, or there was nothing to
  apply, or nothing relevant changed; or
- failed after its apply succeeded: the job `apply` passed and only the
  verification (or the report) behind it failed. What it applied is in the
  state; comparing with the commit before it would select its units again
  and refuse a move of state that has already happened.

A run whose apply failed, was held back (destroys without approval) or never
started does not count, so the units it left out stay selected. Prints
nothing when no such run is found: the whole tree runs then.

Reads the runs and their jobs through `gh api` (GH_TOKEN, actions: read).
"""
import json
import subprocess
import sys

RUNS = 50
JOB_LOOKUPS = 20


def gh(path: str):
    out = subprocess.run(["gh", "api", path], capture_output=True, text=True, check=True).stdout
    return json.loads(out or "{}")


def applied(jobs: list) -> bool:
    """The job apply of the run (named `<caller job> / apply` in a reusable workflow) passed."""
    return any((j.get("name") == "apply" or str(j.get("name", "")).endswith(" / apply"))
               and j.get("conclusion") == "success" for j in jobs)


def pick(runs: list, jobs_of) -> str:
    lookups = 0
    for run in runs:
        if run.get("event") not in ("push", "workflow_dispatch") or run.get("status") != "completed":
            continue
        if run.get("conclusion") == "success":
            return run["head_sha"]
        if run.get("conclusion") != "failure" or lookups >= JOB_LOOKUPS:
            continue
        lookups += 1
        if applied(jobs_of(run["id"])):
            return run["head_sha"]
    return ""


def main(repo: str, workflow: str) -> int:
    runs = gh(f"repos/{repo}/actions/workflows/{workflow}/runs?branch=main&status=completed&per_page={RUNS}")
    runs = runs.get("workflow_runs") or []

    def jobs_of(run_id):
        return gh(f"repos/{repo}/actions/runs/{run_id}/jobs?per_page=100").get("jobs") or []

    print(pick(runs, jobs_of))
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    sys.exit(main(*sys.argv[1:]))
