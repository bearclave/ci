#!/usr/bin/env python3
"""Promotes a commit of an application's staging branch to production.

bearclave/governance standards/workflow.md (applications) is the rule: the
default branch `staging` is what runs in staging, `prod` is what runs in
production and moves only forward, by this script as the GitHub App
bearclave-bot. A promotion is a release:

1. checks: the commit is on staging, prod is its ancestor (a fast-forward),
   the newest finished run of the caller's pipeline for the commit on
   staging succeeded (build, staging deployment and smoke test), and the
   build artifact that production will deploy is still there;
2. fast-forwards prod to the commit (the push deploys that same build to
   production, in the caller's pipeline);
3. tags the commit vX.Y.Z (annotated, by the bot) and publishes a release
   with notes generated from the pull requests.

The version comes from src/release_version.py (VERSION, worked out in the
step before). Every step is skipped when it is already done, so a rerun
after a failure finishes the job: a prod already at the commit is not moved
again, a commit already tagged is not tagged twice.

Reading uses GH_TOKEN (the run's own token, actions and contents read);
writing an installation token of bearclave-bot limited to this repository,
read from Key Vault by github_bot.Bot after the job signed in to Azure.

Environment: GITHUB_REPOSITORY, GH_TOKEN, STAGING_BRANCH, PROD_BRANCH,
WORKFLOW (the caller's pipeline file, e.g. ci.yml), ARTIFACT (name of the
build artifact; empty skips that check), VERSION, SHA (the commit, the
checkout's HEAD).

Usage: promote.py            check, then write as bearclave-bot
       promote.py --dry-run  check and say what would happen
"""
import datetime
import os
import re
import subprocess
import sys

import github_bot as gh

VERSION = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
SHA = re.compile(r"^[0-9a-f]{40}$")
# What the installation token may do, on this repository only. Moving a
# branch to a commit that changes .github/workflows needs `workflows` too.
TOKEN_PERMISSIONS = {"contents": "write", "workflows": "write", "metadata": "read"}


class Refused(Exception):
    """The commit may not be promoted; the message says why and what to do."""


def pick_run(runs: list, branch: str):
    """The newest run of the commit on branch, from a push or a manual run."""
    mine = [r for r in runs if r.get("head_branch") == branch
            and r.get("event") in ("push", "workflow_dispatch")]
    return max(mine, key=lambda r: (r.get("created_at", ""), r.get("id", 0)), default=None)


def check_run(run, sha: str, branch: str, workflow: str) -> None:
    """Refused unless the run exists, is finished and succeeded."""
    if run is None:
        raise Refused(f"no run of {workflow} for {sha[:7]} on {branch}: the commit was never "
                      f"deployed to staging (a newer push may have taken its place). Promote the "
                      f"head of {branch} instead.")
    if run.get("status") != "completed":
        raise Refused(f"run {run.get('html_url')} of {sha[:7]} on {branch} is still "
                      f"{run.get('status')}; promote once it has finished.")
    if run.get("conclusion") != "success":
        raise Refused(f"run {run.get('html_url')} of {sha[:7]} on {branch} ended "
                      f"{run.get('conclusion')}: staging did not get this commit. Fix it on {branch} first.")


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
    'release': vX.Y.Z|None}, or Refused."""
    if not SHA.match(sha):
        raise Refused(f"'{sha}' is not a full commit SHA")
    if not on_staging:
        raise Refused(f"{sha[:7]} is not on the staging branch; only a commit of staging is promoted.")
    if prod_sha and not prod_is_ancestor:
        raise Refused(f"prod ({prod_sha[:7]}) is not an ancestor of {sha[:7]}: prod moves only "
                      f"forward. To roll back, revert on staging and promote the revert.")
    move = None if prod_sha == sha else ("fast-forward" if prod_sha else "create")
    released = sorted((t for t in tags_at_sha if VERSION.match(t)), key=parse)
    if released:
        # A rerun after the tag, or a commit released earlier: only what is missing.
        return {"move": move, "tag": None, "release": released[-1]}
    if not version:
        raise Refused("nothing since the last release calls for one (only docs, test, ci or chore); "
                      "give the version input (vX.Y.Z) to release anyway.")
    if not VERSION.match(version):
        raise Refused(f"'{version}' is not vX.Y.Z")
    if version in existing_tags:
        raise Refused(f"{version} already exists on another commit; give a higher version.")
    return {"move": move, "tag": version, "release": version}


def parse(tag: str) -> tuple:
    return tuple(int(x) for x in VERSION.match(tag).groups())


# --- git and GitHub --------------------------------------------------------------

def git(*args, check=True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=check)


def remote_sha(branch: str) -> str:
    r = git("rev-parse", "--verify", "-q", f"refs/remotes/origin/{branch}^{{commit}}", check=False)
    return r.stdout.strip() if r.returncode == 0 else ""


def is_ancestor(older: str, newer: str) -> bool:
    return git("merge-base", "--is-ancestor", older, newer, check=False).returncode == 0


def main(dry_run: bool) -> int:
    repo = os.environ["GITHUB_REPOSITORY"]
    staging = os.environ["STAGING_BRANCH"]
    prod = os.environ.get("PROD_BRANCH") or "prod"
    workflow = os.environ["WORKFLOW"]
    artifact = os.environ.get("ARTIFACT", "")
    version = os.environ.get("VERSION", "")
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
        if not staging_sha:
            raise Refused(f"the branch {staging} does not exist")
        on_staging = is_ancestor(sha, staging_sha)
        runs = gh.request("GET", f"/repos/{repo}/actions/workflows/{workflow}/runs"
                                 f"?head_sha={sha}&branch={staging}&per_page=100", read)["workflow_runs"]
        run = pick_run(runs, staging)
        check_run(run, sha, staging, workflow)
        gh.say(f"- Staging run: {run['html_url']} ({run['conclusion']})")
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
    gh.say(f"- Tag: {todo['tag'] or 'already tagged ' + todo['release']}")
    gh.say(f"- Release: {todo['release']}")
    if dry_run:
        gh.say("\nDry run: nothing written.")
        return 0

    token = gh.Bot().token(TOKEN_PERMISSIONS, [repo.split("/", 1)[1]])
    # prod first: a tag marks what reached production, never what might.
    if todo["move"] == "create":
        gh.request("POST", f"/repos/{repo}/git/refs", token, {"ref": f"refs/heads/{prod}", "sha": sha})
    elif todo["move"] == "fast-forward":
        # force false: GitHub refuses anything but a fast-forward.
        gh.request("PATCH", f"/repos/{repo}/git/refs/heads/{prod}", token, {"sha": sha, "force": False})
    if todo["move"]:
        gh.say(f"\n{prod} is at {sha[:7]}; its push deploys the build of the staging run to production.")
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
