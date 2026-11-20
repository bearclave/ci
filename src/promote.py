#!/usr/bin/env python3
"""Promotes a commit of an application's staging branch to production.

bearclave/governance standards/workflow.md (applications) is the rule: the
default branch `staging` is what runs in staging, `prod` is what runs in
production and moves only forward, by this script as the GitHub App
bearclave-promote (installed only on the applications; its key in a Key
Vault of its own). A promotion is a release:

1. checks: the commit is on staging and prod is its ancestor (a
   fast-forward); the newest successful run of the caller's pipeline for the
   commit on staging has a successful staging deployment job; the build
   artifact that production will deploy is still there;
2. fast-forwards prod to the commit; its push deploys the build of the
   same run - the production job of static-web-app.yml chooses it by the
   same rule (pick_run, then the job);
3. waits for that production run and, only when it succeeded, tags the
   commit vX.Y.Z (annotated, by the App) and publishes the release with
   notes generated from the pull requests.

The version comes from src/release_version.py (VERSION, worked out in the
step before). Every step is skipped when it is already done, so a rerun
after a failure finishes the job: a prod already at the commit is not moved
again, a commit already tagged is not tagged twice.

Reading uses GH_TOKEN (the run's own token: actions, contents read);
writing an installation token of the App limited to this repository, read
from PROMOTE_VAULT by github_bot.Bot after the job signed in to Azure.

Environment: GITHUB_REPOSITORY, GH_TOKEN, STAGING_BRANCH, PROD_BRANCH,
WORKFLOW (the caller's pipeline file, e.g. ci.yml), STAGING_JOB and
PROD_JOB (the jobs that deploy staging and production, e.g. deploy-staging,
deploy-prod; each must have succeeded), ARTIFACT (the build artifact;
empty skips that check), VERSION, SHA (the checkout's HEAD), PROMOTE_VAULT,
WAIT_MINUTES (for the production run).

Usage: promote.py            check, then write as bearclave-promote
       promote.py --dry-run  check and say what would happen
"""
import datetime
import os
import re
import subprocess
import sys
import time

import github_bot as gh

VERSION = re.compile(r"v[0-9]+\.[0-9]+\.[0-9]+")
SHA = re.compile(r"[0-9a-f]{40}")
VAULT = "kv-ghpromote-sec-weu-001"
# What the installation token may do, on this repository only. Moving a
# branch to a commit that changes .github/workflows needs `workflows` too.
TOKEN_PERMISSIONS = {"contents": "write", "workflows": "write", "metadata": "read"}
POLL_SECONDS = 20
# The job of promote.yml times out after 35 minutes; the wait has to end first.
MAX_WAIT_MINUTES = 30


class Refused(Exception):
    """The commit may not be promoted; the message says why and what to do."""


def is_version(tag: str) -> bool:
    return bool(VERSION.fullmatch(tag or ""))


def parse(tag: str) -> tuple:
    return tuple(int(x) for x in tag[1:].split("."))


def pick_run(runs: list, branch: str):
    """The newest successful run of the commit on branch, from a push or a manual run."""
    good = [r for r in runs if r.get("head_branch") == branch
            and r.get("event") in ("push", "workflow_dispatch")
            and r.get("status") == "completed" and r.get("conclusion") == "success"]
    return max(good, key=lambda r: (r.get("created_at", ""), r.get("id", 0)), default=None)


def check_run(run, sha: str, branch: str, workflow: str) -> None:
    if run is None:
        raise Refused(f"no successful run of {workflow} for {sha[:7]} on {branch}: staging never got "
                      f"this commit (a newer push may have taken its place). Promote the head of {branch}.")


def check_job(jobs: list, name: str) -> None:
    """Refused unless the run's job `name` (in a caller: `pipeline / name`) succeeded."""
    mine = [j for j in jobs if j.get("name", "").split(" / ")[-1] == name]
    if not mine:
        raise Refused(f"the staging run has no job '{name}': it did not deploy staging.")
    if not all(j.get("conclusion") == "success" for j in mine):
        raise Refused(f"the job '{name}' of the staging run did not succeed "
                      f"({', '.join(str(j.get('conclusion')) for j in mine)}).")


def check_artifact(artifacts: list, name: str, now: datetime.datetime) -> dict:
    """The live artifact called name, or Refused; production deploys it."""
    found = [a for a in artifacts if a.get("name") == name]
    if not found:
        raise Refused(f"the run has no artifact '{name}': production would have nothing to deploy.")
    live = [a for a in found if not a.get("expired")]
    if not live:
        raise Refused(f"the artifact '{name}' has expired: promote a newer commit of staging "
                      f"(it contains this one).")
    art = max(live, key=lambda a: a.get("created_at", ""))
    expires = art.get("expires_at")
    if expires:
        left = datetime.datetime.fromisoformat(expires.replace("Z", "+00:00")) - now
        if left < datetime.timedelta(hours=1):
            raise Refused(f"the artifact '{name}' expires within an hour ({expires}); "
                          f"promote a newer commit of staging.")
    return art


def plan(sha: str, prod_sha: str, prod_is_ancestor: bool, on_staging: bool,
         tags_at_sha: list, version: str, existing_tags: list) -> dict:
    """What to do: {'move': 'create'|'fast-forward'|None, 'tag': vX.Y.Z|None,
    'release': vX.Y.Z}, or Refused."""
    if not SHA.fullmatch(sha or ""):
        raise Refused(f"'{sha}' is not a full commit SHA")
    if not on_staging:
        raise Refused(f"{sha[:7]} is not on the staging branch; only a commit of staging is promoted.")
    if prod_sha and not prod_is_ancestor:
        raise Refused(f"prod ({prod_sha[:7]}) is not an ancestor of {sha[:7]}: prod moves only "
                      f"forward. To roll back, revert on staging and promote the revert.")
    move = None if prod_sha == sha else ("fast-forward" if prod_sha else "create")
    released = sorted((t for t in tags_at_sha if is_version(t)), key=parse)
    if released and version and version not in released:
        raise Refused(f"{sha[:7]} is already released as {released[-1]}; it gets no second version "
                      f"({version}). Leave the version input empty to finish that release.")
    if released:
        # A rerun after the tag, or a commit released earlier: only what is missing.
        return {"move": move, "tag": None, "release": released[-1]}
    if not version:
        raise Refused("nothing since the last release calls for one (only docs, test, ci or chore); "
                      "give the version input (vX.Y.Z) to release anyway.")
    if not is_version(version):
        raise Refused(f"'{version}' is not vX.Y.Z")
    if version in existing_tags:
        raise Refused(f"{version} already exists on another commit; give a higher version.")
    return {"move": move, "tag": version, "release": version}


def wait_minutes(value) -> int:
    """The minutes to wait for the production run, or Refused."""
    try:
        minutes = int(value)
    except (TypeError, ValueError):
        raise Refused(f"wait-minutes '{value}' is not a number") from None
    if not 0 <= minutes <= MAX_WAIT_MINUTES:
        raise Refused(f"wait-minutes is {minutes}; it must be 0 to {MAX_WAIT_MINUTES}, because the job "
                      f"times out after 35 minutes")
    return minutes


def prod_state(runs: list, branch: str):
    """(state, run) of the newest production run of the commit: 'none',
    'pending', 'success' or 'failure'."""
    mine = [r for r in runs if r.get("head_branch") == branch and r.get("event") in ("push", "workflow_dispatch")]
    run = max(mine, key=lambda r: (r.get("created_at", ""), r.get("id", 0)), default=None)
    if run is None:
        return "none", None
    if run.get("status") != "completed":
        return "pending", run
    return ("success" if run.get("conclusion") == "success" else "failure"), run


# --- git and GitHub --------------------------------------------------------------

def git(*args, check=True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=check)


def remote_sha(branch: str) -> str:
    r = git("rev-parse", "--verify", "-q", f"refs/remotes/origin/{branch}^{{commit}}", check=False)
    return r.stdout.strip() if r.returncode == 0 else ""


def is_ancestor(older: str, newer: str) -> bool:
    return git("merge-base", "--is-ancestor", older, newer, check=False).returncode == 0


def runs_of(repo: str, workflow: str, sha: str, branch: str, token: str) -> list:
    return gh.request("GET", f"/repos/{repo}/actions/workflows/{workflow}/runs"
                             f"?head_sha={sha}&branch={branch}&per_page=100", token)["workflow_runs"]


def wait_for_prod(repo: str, workflow: str, sha: str, prod: str, token: str, minutes: int, sleep=time.sleep):
    """The successful production run of the commit, or Refused."""
    deadline = time.monotonic() + minutes * 60
    while True:
        state, run = prod_state(runs_of(repo, workflow, sha, prod, token), prod)
        if state == "success":
            return run
        if state == "failure":
            raise Refused(f"the production run {run.get('html_url')} ended {run.get('conclusion')}: "
                          f"nothing is tagged. Fix it (a transient error: run {workflow} by hand on "
                          f"{prod}), then promote again - prod is already at the commit.")
        if time.monotonic() > deadline:
            raise Refused(f"the production run of {sha[:7]} did not finish within {minutes} minutes "
                          f"({state}); promote again once it is green, it then tags.")
        sleep(POLL_SECONDS)


def main(dry_run: bool) -> int:
    repo = os.environ["GITHUB_REPOSITORY"]
    staging = os.environ["STAGING_BRANCH"]
    prod = os.environ.get("PROD_BRANCH") or "prod"
    workflow = os.environ["WORKFLOW"]
    staging_job = os.environ.get("STAGING_JOB", "")
    artifact = os.environ.get("ARTIFACT", "")
    version = os.environ.get("VERSION", "")
    vault = os.environ.get("PROMOTE_VAULT") or VAULT
    prod_job = os.environ.get("PROD_JOB", "")
    sha = os.environ.get("SHA") or git("rev-parse", "HEAD").stdout.strip()
    read = os.environ["GH_TOKEN"]

    # The checkout (fetch-depth 0) brought every branch and tag; it keeps no
    # credentials, so nothing is fetched here.
    staging_sha = remote_sha(staging)
    prod_sha = remote_sha(prod)

    gh.say(f"## Promote {sha[:7]} of {repo}{' (dry run)' if dry_run else ''}")
    gh.say()
    gh.say(f"- {staging}: {staging_sha[:7] or 'missing'}, {prod}: {prod_sha[:7] or 'not created yet'}")
    try:
        if not SHA.fullmatch(sha):
            raise Refused(f"'{sha}' is not a full commit SHA")
        minutes = wait_minutes(os.environ.get("WAIT_MINUTES") or 20)
        if not staging_sha:
            raise Refused(f"the branch {staging} does not exist")
        on_staging = is_ancestor(sha, staging_sha)
        run = pick_run(runs_of(repo, workflow, sha, staging, read), staging)
        check_run(run, sha, staging, workflow)
        gh.say(f"- Staging run: {run['html_url']}")
        if staging_job:
            jobs = gh.request("GET", f"/repos/{repo}/actions/runs/{run['id']}/jobs?per_page=100", read)["jobs"]
            check_job(jobs, staging_job)
            gh.say(f"- Its job {staging_job}: success")
        if artifact:
            arts = gh.request("GET", f"/repos/{repo}/actions/runs/{run['id']}/artifacts?per_page=100",
                              read)["artifacts"]
            art = check_artifact(arts, artifact, datetime.datetime.now(datetime.timezone.utc))
            gh.say(f"- Build artifact: '{artifact}' of that run, expires {art.get('expires_at')}")
        tags_at = git("tag", "--points-at", sha).stdout.split()
        existing = git("tag", "--list", "v*").stdout.split()
        todo = plan(sha, prod_sha, is_ancestor(prod_sha, sha) if prod_sha else True,
                    on_staging, tags_at, version, existing)
    except Refused as e:
        print(f"::error title=promote::{e}")
        gh.say(f"\n**Refused:** {e}")
        return 1

    gh.say(f"- {prod}: {todo['move'] or 'already at the commit'}")
    gh.say(f"- Tag: {todo['tag'] or 'already tagged ' + todo['release']}, once production is deployed")
    gh.say(f"- Release: {todo['release']}")
    if dry_run:
        gh.say("\nDry run: nothing written.")
        return 0

    token = gh.Bot(vault).token(TOKEN_PERMISSIONS, [repo.split("/", 1)[1]])
    print(f"::add-mask::{token}")
    if todo["move"] == "create":
        gh.request("POST", f"/repos/{repo}/git/refs", token, {"ref": f"refs/heads/{prod}", "sha": sha})
    elif todo["move"] == "fast-forward":
        # force false: GitHub refuses anything but a fast-forward.
        gh.request("PATCH", f"/repos/{repo}/git/refs/heads/{prod}", token, {"sha": sha, "force": False})
    if todo["move"]:
        gh.say(f"\n{prod} is at {sha[:7]}; waiting for its production run.")
    try:
        prod_run = wait_for_prod(repo, workflow, sha, prod, read, minutes)
        if prod_job:
            # A green run whose deployment was skipped deployed nothing.
            jobs = gh.request("GET", f"/repos/{repo}/actions/runs/{prod_run['id']}/jobs?per_page=100", read)["jobs"]
            check_job(jobs, prod_job)
    except Refused as e:
        print(f"::error title=promote::{e}")
        gh.say(f"\n**Not released:** {e}")
        return 1
    gh.say(f"Production run: {prod_run['html_url']} (success)")
    if todo["tag"]:
        tag = gh.request("POST", f"/repos/{repo}/git/tags", token,
                         {"tag": todo["tag"], "message": todo["tag"], "object": sha, "type": "commit"})
        gh.request("POST", f"/repos/{repo}/git/refs", token,
                   {"ref": f"refs/tags/{todo['tag']}", "sha": tag["sha"]})
        gh.say(f"Tagged {todo['tag']}.")
    try:
        existing_release = gh.request("GET", f"/repos/{repo}/releases/tags/{todo['release']}", token)
    except gh.NotFound:
        existing_release = None
    if existing_release:
        gh.say(f"Release {todo['release']}: {existing_release.get('html_url')} (already published)")
    else:
        rel = gh.request("POST", f"/repos/{repo}/releases", token,
                         {"tag_name": todo["release"], "name": todo["release"],
                          "generate_release_notes": True, "make_latest": "true"})
        gh.say(f"Release {todo['release']}: {rel.get('html_url')}")
    return 0


if __name__ == "__main__":
    sys.exit(main("--dry-run" in sys.argv[1:]))
