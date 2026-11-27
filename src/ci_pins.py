#!/usr/bin/env python3
"""Bumps the exact pins of bearclave/ci in the other repositories to a release.

Run by .github/workflows/ci-pins.yml when a release of this repository is
published, as the GitHub App bearclave-bot (see github_bot.py). Repositories
that touch production call `bearclave/ci/.github/workflows/<name>.yml@vX.Y.Z`
instead of following the major tag; Dependabot does not read these pins, so
this opens the bump: one pull request per repository whose
workflows pin an older release of the same major, from the branch deps/ci,
labelled type:deps and assigned to the owner, who approves and merges it
after its plan.

The repositories are found by reading .github/workflows/ of every repository
of the organisation through the API (the code search index can lag behind).
A pull request still open on deps/ci is moved to the new release (the branch
is rebuilt from the default branch, the title and description rewritten).
Calls of `@v2` and of another major are left alone.

A pin is not moved when nothing it calls has changed: every pull request
costs a whole-tree plan in a Terragrunt repository, and most releases touch a
workflow the repository does not use. The workflows a repository calls are
compared between its pin and the new release (the compare API); only when one
of them differs are all its pins bumped. A comparison that cannot be read, or
is truncated, counts as a change. A release whose tag is not an annotated,
verified signed tag is refused, as it is for the major tag (major_tag.py).

Usage: ci_pins.py vX.Y.Z             open or update the pull requests (bot)
       ci_pins.py --dry-run vX.Y.Z   only print the diffs; reads with GH_TOKEN
                                     when it is set, else as the bot
"""
import difflib
import os
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import github_bot as gh  # noqa: E402
import major_tag  # noqa: E402

THIS_REPO = "ci"
SKIP = {THIS_REPO}
BRANCH = "deps/ci"
RELEASE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
PIN = re.compile(r"(bearclave/ci/\.github/workflows/[\w.-]+\.ya?ml)@(v\d+\.\d+\.\d+)\b")


def version(tag: str):
    m = RELEASE.match(tag)
    return tuple(int(x) for x in m.groups()) if m else None


def bump(text: str, tag: str) -> tuple:
    """(new text, sorted old versions replaced) - every pin of bearclave/ci to
    an older release of tag's major moves to tag."""
    new = version(tag)
    old = set()

    def repl(m):
        cur = version(m.group(2))
        if cur is None or cur[0] != new[0] or cur >= new:
            return m.group(0)
        old.add(m.group(2))
        return f"{m.group(1)}@{tag}"
    out = PIN.sub(repl, text)
    return out, sorted(old, key=version)


def check_release(tag: str, release: dict, releases: list) -> None:
    """Refuses a tag that is not the newest published release of its major."""
    if version(tag) is None:
        sys.exit(f"refused: not a release tag: {tag!r} (vX.Y.Z)")
    if release.get("draft") or release.get("prerelease"):
        sys.exit(f"refused: {tag} is a draft or a pre-release")
    major = version(tag)[0]
    newer = [r["tag_name"] for r in releases
             if not r.get("draft") and not r.get("prerelease") and version(r.get("tag_name", ""))
             and version(r["tag_name"])[0] == major and version(r["tag_name"]) > version(tag)]
    if newer:
        sys.exit(f"refused: {max(newer, key=version)} is newer than {tag}")


def check_signed(tag: str, release: dict, token: str) -> None:
    """Refuses a tag that is lightweight or not verified signed (the rule of major_tag.py)."""
    try:
        ref = gh.request("GET", f"/repos/{gh.ORG}/{THIS_REPO}/git/ref/tags/{tag}", token)
        tag_object = (gh.request("GET", f"/repos/{gh.ORG}/{THIS_REPO}/git/tags/{ref['object']['sha']}", token)
                      if ref["object"]["type"] == "tag" else {})
    except gh.NotFound:
        sys.exit(f"refused: no tag {tag}")
    try:
        major_tag.check_release(tag, release, ref, tag_object)
    except major_tag.Refused as e:
        sys.exit(f"refused: {e}")


# The compare API lists at most 300 files; a comparison that long is not read.
COMPARE_LIMIT = 300


def called(text: str, tag: str) -> dict:
    """{workflow file name: the older pin} of every call a bump would move."""
    new = version(tag)
    out = {}
    for m in PIN.finditer(text):
        cur = version(m.group(2))
        if cur is not None and cur[0] == new[0] and cur < new:
            out[m.group(1).rsplit("/", 1)[1]] = m.group(2)
    return out


def changed_workflows(old: str, tag: str, token: str, cache: dict):
    """The names of the workflow files that differ between two releases, or None
    when that cannot be told (an unreadable or truncated comparison)."""
    if (old, tag) not in cache:
        try:
            data = gh.request("GET", f"/repos/{gh.ORG}/{THIS_REPO}/compare/{old}...{tag}", token)
            files = data.get("files") or []
            cache[(old, tag)] = None if len(files) >= COMPARE_LIMIT else {
                f["filename"].rsplit("/", 1)[1] for f in files
                if f["filename"].startswith(".github/workflows/")}
        except (gh.NotFound, gh.ApiError):
            cache[(old, tag)] = None
    return cache[(old, tag)]


def calls_a_changed_workflow(texts: list, tag: str, token: str, cache: dict) -> bool:
    """True when a workflow some pin calls differs between that pin and tag."""
    for text in texts:
        for name, old in called(text, tag).items():
            changed = changed_workflows(old, tag, token, cache)
            if changed is None or name in changed:
                return True
    return False


def title(tag: str) -> str:
    return f"deps: bump bearclave/ci to {tag}"


def body(tag: str, url: str, old: list, files: list) -> str:
    return "\n".join([
        "## Why", "",
        f"`bearclave/ci` {tag} is released ({url}). This repository pins the shared workflows to an exact "
        "release, so a change reaches it only through a pull request with its pipeline run.", "",
        "## What changes", "",
        f"- `bearclave/ci` {', '.join(old)} -> {tag} in " + ", ".join(f"`{f}`" for f in files), "",
        "## Impact", "",
        "- Production: the pipeline of this pull request runs with the new workflows; "
        "read the release notes for what changed",
        "- Destroyed or replaced: see the plan, where there is one",
        "- Manual steps after merge: none", "",
        "## Verification", "",
        "The checks of this pull request run on the new release.", "",
        "## Review", "",
        "Opened by `bearclave-bot` (`ci-pins.yml` in `bearclave/ci`); the owner approves and merges.", "",
        "## Links", "",
        f"Release notes: {url}", "",
    ])


def workflow_files(repo: str, ref: str, token: str) -> dict:
    """{path: text} of the repository's .github/workflows/*.yml at ref."""
    try:
        listing = gh.request("GET", f"/repos/{gh.ORG}/{repo}/contents/.github/workflows?ref={ref}", token)
    except gh.NotFound:
        return {}
    out = {}
    for item in listing:
        if item.get("type") == "file" and item["name"].endswith((".yml", ".yaml")):
            out[item["path"]] = gh.file_text(f"{gh.ORG}/{repo}", item["path"], ref, token) or ""
    return out


def main(argv: list) -> int:
    dry_run = "--dry-run" in argv
    args = [a for a in argv if a != "--dry-run"]
    if len(args) != 1:
        sys.exit(__doc__)
    tag = args[0]
    if version(tag) is None:
        sys.exit(f"refused: not a release tag: {tag!r} (vX.Y.Z)")

    bot = None
    if dry_run and os.environ.get("GH_TOKEN"):
        read_token = os.environ["GH_TOKEN"]
    else:
        bot = gh.Bot()
        read_token = bot.token(gh.READ)

    try:
        release = gh.request("GET", f"/repos/{gh.ORG}/{THIS_REPO}/releases/tags/{tag}", read_token)
    except gh.NotFound:
        sys.exit(f"refused: no published release {tag}")
    check_release(tag, release, gh.request("GET", f"/repos/{gh.ORG}/{THIS_REPO}/releases?per_page=100", read_token))
    check_signed(tag, release, read_token)
    compared = {}

    gh.say(f"## Pins of bearclave/ci -> {tag}{' (dry run)' if dry_run else ''}")
    gh.say()
    done = 0
    for r in gh.repositories(read_token):
        name, base = r["name"], r["default_branch"]
        if name in SKIP or r.get("fork"):
            continue
        base_sha = gh.request("GET", f"/repos/{gh.ORG}/{name}/commits/{base}", read_token)["sha"]
        changed, old = {}, set()
        files = workflow_files(name, base_sha, read_token)
        if not calls_a_changed_workflow(list(files.values()), tag, read_token, compared):
            if any(called(t, tag) for t in files.values()):
                gh.say(f"- {name}: skipped, nothing it calls changed in {tag}")
            continue
        for path, text in sorted(files.items()):
            new_text, replaced = bump(text, tag)
            if replaced:
                changed[path] = new_text
                old.update(replaced)
                if dry_run:
                    sys.stdout.writelines(difflib.unified_diff(
                        text.splitlines(True), new_text.splitlines(True), f"a/{name}/{path}", f"b/{name}/{path}"))
        if not changed:
            continue
        old = sorted(old, key=version)
        text = body(tag, release.get("html_url", ""), old, sorted(changed))
        if dry_run:
            gh.say(f"- {name}: would bump {', '.join(old)} -> {tag} in {len(changed)} file(s)")
            done += 1
            continue
        token = bot.token(gh.WRITE, [name])
        message = f"{title(tag)}\n\n{', '.join(old)} -> {tag} in {', '.join(sorted(changed))}\n"
        existing = gh.open_pull(f"{gh.ORG}/{name}", BRANCH, token)
        gh.commit_files(f"{gh.ORG}/{name}", token, base_sha, BRANCH, changed, message)
        if existing:
            gh.request("PATCH", f"/repos/{gh.ORG}/{name}/pulls/{existing['number']}", token,
                       {"title": title(tag), "body": text})
            gh.say(f"- {name}: #{existing['number']} moved to {tag}")
        else:
            pr = gh.create_pull(f"{gh.ORG}/{name}", token, BRANCH, base, title(tag), text)
            gh.say(f"- {name}: opened {pr['html_url']}")
        done += 1
    gh.say()
    gh.say(f"Repositories: {done}" + (" (dry run: nothing written)" if dry_run else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
