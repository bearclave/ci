#!/usr/bin/env python3
"""The next release of a repository, from the commits on main since its last tag.

bearclave/governance standards/versioning.md is the rule: every commit on main
is a squashed pull request whose subject is the pull request title, so the
Conventional Commit types since the last vX.Y.Z decide the bump:

- `!` after the type or a `BREAKING CHANGE:` footer -> major (minor before v1.0.0)
- otherwise `feat` -> minor
- otherwise `fix`, `perf`, `refactor`, `deps`, `revert` -> patch
- only `docs`, `test`, `ci`, `chore` -> no release

A repository without a tag starts at v1.0.0. An explicit version overrides the
calculation; it has to be a vX.Y.Z above the last tag.

Writes `version` (empty when no release is needed), `previous` and `bump` to
GITHUB_OUTPUT and a preview of the notes to GITHUB_STEP_SUMMARY.

Usage: release_version.py [<vX.Y.Z>]   (in the checkout, full history)
       release_version.py --self-test
"""
import os
import re
import subprocess
import sys
import tempfile

TITLE = re.compile(r"^(?P<type>[a-z]+)(?:\([^)]*\))?(?P<breaking>!)?: \S")
VERSION = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
FOOTER = re.compile(r"^BREAKING[ -]CHANGE: ", re.M)
PATCH = ("fix", "perf", "refactor", "deps", "revert")
NONE = ("docs", "test", "ci", "chore")
ORDER = ("major", "minor", "patch", "none")
# The sections of .github/release.yml, by commit type.
SECTIONS = (("Breaking changes", None), ("Features", ("feat",)), ("Fixes", ("fix", "revert")),
            ("Performance", ("perf",)), ("Dependencies", ("deps",)))


def parse(version: str) -> tuple:
    m = VERSION.match(version)
    if not m:
        raise ValueError(f"'{version}' is not vX.Y.Z")
    return tuple(int(x) for x in m.groups())


def bump_of(subject: str, body: str) -> str:
    """major, minor, patch, none - or other for a subject that is no Conventional Commit."""
    m = TITLE.match(subject)
    if not m:
        return "other"
    if m.group("breaking") or FOOTER.search(body or ""):
        return "major"
    kind = m.group("type")
    if kind == "feat":
        return "minor"
    if kind in PATCH:
        return "patch"
    if kind in NONE:
        return "none"
    return "other"


def next_version(previous: str, bumps: list) -> tuple:
    """(version, bump); version is empty when nothing calls for a release."""
    if not previous:
        return "v1.0.0", "first"
    found = [b for b in ORDER if b in bumps]
    bump = found[0] if found else "none"
    major, minor, patch = parse(previous)
    if bump == "major" and major == 0:
        bump = "minor"
    if bump == "major":
        return f"v{major + 1}.0.0", bump
    if bump == "minor":
        return f"v{major}.{minor + 1}.0", bump
    if bump == "patch":
        return f"v{major}.{minor}.{patch + 1}", bump
    return "", bump


def notes(commits: list) -> list:
    """Markdown preview of the notes, grouped like .github/release.yml."""
    lines = []
    used = set()
    for title, types in SECTIONS:
        picked = [i for i, (s, b) in enumerate(commits) if i not in used and (
            bump_of(s, b) == "major" if types is None else
            (TITLE.match(s) and TITLE.match(s).group("type") in types))]
        if picked:
            lines += ["", f"### {title}"] + [f"- {commits[i][0]}" for i in picked]
            used.update(picked)
    rest = [s for i, (s, _) in enumerate(commits) if i not in used]
    if rest:
        lines += ["", "### Other"] + [f"- {s}" for s in rest]
    return lines


def git(*args) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout


def last_tag() -> str:
    tags = [t for t in git("tag", "--list", "v*.*.*", "--merged", "HEAD").split() if VERSION.match(t)]
    return max(tags, key=parse) if tags else ""


def commits_since(tag: str) -> list:
    span = [f"{tag}..HEAD"] if tag else ["HEAD"]
    raw = git("log", "--first-parent", "--format=%s%x1f%b%x1e", *span)
    return [tuple(c.strip("\n").split("\x1f", 1)) for c in raw.split("\x1e") if c.strip()]


def main(wanted: str) -> int:
    previous = last_tag()
    commits = commits_since(previous)
    bumps = [bump_of(s, b) for s, b in commits]
    version, bump = next_version(previous, bumps)
    if wanted:
        try:
            parse(wanted)
            if previous and parse(wanted) <= parse(previous):
                raise ValueError(f"{wanted} is not above the last tag {previous}")
        except ValueError as e:
            print(f"::error::{e}")
            return 1
        version, bump = wanted, "given"

    others = [s for (s, _), b in zip(commits, bumps) if b == "other"]
    summary = [f"## Release of {os.environ.get('GITHUB_REPOSITORY', 'this repository')}", "",
               f"- Last tag: {previous or 'none'}",
               f"- Pull requests since: {len(commits)}",
               f"- Bump: {bump}",
               f"- Next version: **{version or 'none, no release needed'}**"]
    if others:
        summary += ["", f"{len(others)} commit(s) are not Conventional Commits and do not count "
                        "for the bump (history from before the standards)."]
    if version:
        summary += ["", "## Notes (preview; the release generates them from the labels)"] + notes(commits)
        summary += ["", "## Signed tag by hand", "", "```bash",
                    "git switch main && git pull --ff-only",
                    f'git tag -s {version} -m "{version}" && git push origin {version}',
                    f"gh release create {version} --verify-tag --generate-notes", "```"]
    for w in others[:20]:
        print(f"::warning::Not a Conventional Commit, not counted: {w}")
    print("\n".join(summary))
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write("\n".join(summary) + "\n")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
            f.write(f"version={version}\nprevious={previous}\nbump={bump}\n")
    return 0


def self_test() -> int:
    assert bump_of("feat(x): add y", "") == "minor"
    assert bump_of("fix!: drop z", "") == "major"
    assert bump_of("refactor: move", "Why\n\nBREAKING CHANGE: input renamed") == "major"
    assert bump_of("deps: bump a from 1 to 2 (#3)", "") == "patch"
    assert bump_of("docs: readme", "") == "none"
    assert bump_of("Kopie sdilenych workflow (#5)", "") == "other"
    assert next_version("", []) == ("v1.0.0", "first")
    assert next_version("v2.4.1", ["patch", "minor", "none"]) == ("v2.5.0", "minor")
    assert next_version("v2.4.1", ["major"]) == ("v3.0.0", "major")
    assert next_version("v0.3.1", ["major"]) == ("v0.4.0", "minor")
    assert next_version("v2.4.1", ["none", "other"]) == ("", "none")
    with tempfile.TemporaryDirectory() as d:
        os.chdir(d)
        git("init", "-q", "-b", "main")
        for msg in ("chore: init", "feat: add a (#1)", "fix: b (#2)"):
            git("-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false",
                "commit", "-q", "--allow-empty", "-m", msg)
            if msg == "feat: add a (#1)":
                git("-c", "tag.gpgsign=false", "tag", "v1.2.0")
        assert last_tag() == "v1.2.0" and [s for s, _ in commits_since("v1.2.0")] == ["fix: b (#2)"]
    print("self-test ok")
    return 0


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        sys.exit(self_test())
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else ""))
