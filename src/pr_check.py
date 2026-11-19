#!/usr/bin/env python3
"""The `pr` check: a pull request follows the standards of the organisation.

bearclave/governance standards/workflow.md is the rule this enforces:

- the title is a Conventional Commit (it becomes the commit on main); a bot
  title over 72 characters only warns, since the bot writes it itself
  (Dependabot: "... in the actions group");
- the branch is <type>/<slug> (bot branches excepted);
- the description keeps the sections of the template (people only);

and it keeps the pull request tidy:

- type:*, breaking and size:* labels follow the title and the diff;
- a pull request nobody is assigned to is assigned to its author.

Reads the event from the environment (PR_NUMBER, PR_TITLE, PR_BODY,
PR_BRANCH, PR_AUTHOR, PR_ASSIGNEES, PR_LABELS, PR_HEAD_SHA, GITHUB_REPOSITORY)
and talks to GitHub through `gh` with GH_TOKEN. Exit code 1 when a rule is
broken; the labels and the assignee are set either way.

Usage: pr_check.py            (in a workflow)
       pr_check.py --self-test
"""
import fnmatch
import json
import os
import re
import subprocess
import sys

TYPES = ("feat", "fix", "perf", "refactor", "docs", "test", "ci", "chore", "deps", "revert")
TITLE = re.compile(
    r"^(?P<type>" + "|".join(TYPES) + r")"
    r"(?:\((?P<scope>[a-z0-9][a-z0-9._/-]*)\))?"
    r"(?P<breaking>!)?: (?P<summary>\S.*)$"
)
BRANCH = re.compile(r"^(?:" + "|".join(TYPES) + r")/[a-z0-9][a-z0-9-]*$")
BOT_BRANCH = re.compile(r"^(?:dependabot/.+|chore/tombstones)$")
SECTIONS = ("Why", "What changes", "Impact", "Verification", "Review", "Links")
SIZES = (("size:xs", 10), ("size:s", 50), ("size:m", 200), ("size:l", 500), ("size:xl", None))
# Never counted: lock files and what .gitattributes marks linguist-generated.
NOT_COUNTED = ("*package-lock.json", "*.lock", "*.terraform.lock.hcl", "*uv.lock", "*poetry.lock")


def title_too_long(title: str) -> list:
    if len(title) > 72:
        return [f"The title is {len(title)} characters; at most 72 (it becomes the commit subject)."]
    return []


def title_problems(title: str) -> list:
    """Format of the title; its length is title_too_long()."""
    m = TITLE.match(title)
    if not m:
        return [f"The title '{title}' is not '<type>(<scope>)!: <summary>' with type one of "
                f"{', '.join(TYPES)}."]
    problems = []
    summary = m.group("summary")
    if summary[0].isupper() and not summary.split()[0].isupper():
        problems.append("The summary starts with a lowercase letter (imperative: 'add ...', not 'Add ...').")
    if summary.endswith("."):
        problems.append("The title ends without a full stop.")
    return problems


def branch_problems(branch: str) -> list:
    if BRANCH.match(branch) or BOT_BRANCH.match(branch):
        return []
    return [f"The branch '{branch}' is not '<type>/<slug>' (lowercase, a-z 0-9 and -). "
            "Rename it: git branch -m <type>/<slug>, push, open a new pull request."]


def body_problems(body: str) -> list:
    missing = [s for s in SECTIONS if not re.search(rf"^##\s+{re.escape(s)}\s*$", body or "", re.M)]
    if not missing:
        return []
    return ["The description is missing the section(s) " + ", ".join(f"'## {s}'" for s in missing)
            + " of the template (write 'none' rather than delete a section)."]


def generated_patterns(gitattributes: str) -> list:
    patterns = []
    for line in gitattributes.splitlines():
        parts = line.split()
        if len(parts) >= 2 and not parts[0].startswith("#") and (
                "linguist-generated" in parts[1:] or "linguist-generated=true" in parts[1:]):
            patterns.append(parts[0])
    return patterns


def counted(path: str, patterns: list) -> bool:
    for pat in list(NOT_COUNTED) + patterns:
        pat = pat.lstrip("/")
        if fnmatch.fnmatch(path, pat) or fnmatch.fnmatch(path, pat.rstrip("/") + "/*"):
            return False
    return True


def size_label(lines: int) -> str:
    for label, limit in SIZES:
        if limit is None or lines < limit:
            return label
    return SIZES[-1][0]


def wanted_labels(title: str, lines: int) -> set:
    labels = {size_label(lines)}
    m = TITLE.match(title)
    if m:
        labels.add(f"type:{m.group('type')}")
        if m.group("breaking"):
            labels.add("breaking")
    return labels


def managed(label: str) -> bool:
    return label.startswith(("type:", "size:")) or label == "breaking"


def gh(*args, check=True) -> str:
    r = subprocess.run(["gh", *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout


def main() -> int:
    repo = os.environ["GITHUB_REPOSITORY"]
    number = os.environ["PR_NUMBER"]
    title = os.environ.get("PR_TITLE", "")
    author = os.environ.get("PR_AUTHOR", "")
    bot = author.endswith("[bot]")

    problems = title_problems(title) + branch_problems(os.environ.get("PR_BRANCH", ""))
    warnings = []
    if bot:
        # Shorten the title before the merge; the squash commit takes it.
        warnings += title_too_long(title)
    else:
        problems += title_too_long(title) + body_problems(os.environ.get("PR_BODY", ""))

    patterns = []
    try:
        raw = gh("api", f"repos/{repo}/contents/.gitattributes?ref={os.environ.get('PR_HEAD_SHA', '')}",
                 "-H", "Accept: application/vnd.github.raw")
        patterns = generated_patterns(raw)
    except RuntimeError:
        pass  # no .gitattributes
    files = json.loads(gh("api", "--paginate", "--slurp", f"repos/{repo}/pulls/{number}/files?per_page=100"))
    lines = sum(f["additions"] + f["deletions"] for page in files for f in page if counted(f["filename"], patterns))

    have = {x for x in os.environ.get("PR_LABELS", "").split(",") if x}
    want = wanted_labels(title, lines)
    add = sorted(want - have)
    remove = sorted(x for x in have - want if managed(x))
    assign = not os.environ.get("PR_ASSIGNEES") and not bot

    summary = [f"## Pull request #{number}", "",
               f"- Changed lines counted for the size: **{lines}** ({size_label(lines)})",
               f"- Labels: {', '.join(sorted(want))}"]

    # Dependabot's token is read-only; its pull requests get their labels and
    # assignee from dependabot.yml.
    if bot:
        summary.append("- Labels and assignee left to the bot's own configuration.")
    else:
        edit = []
        for label in add:
            edit += ["--add-label", label]
        for label in remove:
            edit += ["--remove-label", label]
        if assign:
            edit += ["--add-assignee", author]
        if edit:
            try:
                gh("pr", "edit", number, "--repo", repo, *edit)
            except RuntimeError as e:
                # A label iac-github has not created yet must not fail the check.
                print(f"::warning::Could not update labels or assignee: {e}")

    for w in warnings:
        print(f"::warning::{w}")
    for p in problems:
        print(f"::error::{p}")
    summary += ["", "### Problems" if problems else "Title, branch and description follow the standard."]
    summary += [f"- {p}" for p in problems]
    if warnings:
        summary += ["", "### Warnings"] + [f"- {w}" for w in warnings]
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write("\n".join(summary) + "\n")
    print("\n".join(summary))
    return 1 if problems else 0


def self_test() -> int:
    assert not title_problems("feat(conditional-access): block legacy authentication")
    assert not title_problems("fix!: drop the old input")
    assert not title_problems("deps: bump actions/checkout from 7.0.1 to 7.0.2")
    assert not title_problems("ci(m365): run PSScriptAnalyzer on changed files")
    assert title_problems("Block legacy authentication")
    assert title_problems("feature: x")
    assert title_problems("feat: Block it")
    assert title_problems("feat: block it.")
    long = "deps: bump actions/upload-artifact from 7.0.1 to 7.0.2 in the actions group"
    assert not title_problems(long) and title_too_long(long) and not title_too_long("fix: x")
    assert not branch_problems("feat/42-ca-block-legacy")
    assert not branch_problems("dependabot/github_actions/actions-123")
    assert not branch_problems("chore/tombstones")
    assert branch_problems("feature/x") and branch_problems("feat/Upper") and branch_problems("worktree-x")
    body = "\n".join(f"## {s}\n\nnone\n" for s in SECTIONS)
    assert not body_problems(body) and body_problems("## Why\n")
    assert generated_patterns("ARM/_kql/** linguist-generated\n*.md text\n") == ["ARM/_kql/**"]
    assert not counted("ARM/_kql/a/b.kql", ["ARM/_kql/**"]) and not counted("api/package-lock.json", [])
    assert counted("src/main.tf", ["ARM/_kql/**"])
    assert size_label(0) == "size:xs" and size_label(199) == "size:m" and size_label(500) == "size:xl"
    assert wanted_labels("feat(x)!: y", 3) == {"type:feat", "breaking", "size:xs"}
    print("self-test ok")
    return 0


if __name__ == "__main__":
    sys.exit(self_test() if "--self-test" in sys.argv else main())
