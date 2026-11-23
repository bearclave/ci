#!/usr/bin/env python3
"""Warns about imports.hcl and moves.hcl that outlived the pull request that needed them.

Usage: stale-temp-files.py [<base>]

imports.hcl and moves.hcl next to a unit exist only in the pull request that
takes objects over or renames them; the next pull request after the apply
deletes them, so that the code looks freshly created. A file is stale when
the commit that added it is <base> or one of its ancestors: on a push to
main <base> is the commit the last applied run went through with, so the
file of the very pull request being applied is not; in the weekly drift run
<base> is HEAD (default), so every file still there is. Needs the history
(fetch-depth 0). A warning only: the apply of the pull request that added the
file may still be waiting, and nothing here changes a plan.
"""
import os
import subprocess
import sys

TEMPORARY = ("imports.hcl", "moves.hcl")


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=check)


def tracked() -> list:
    out = git("ls-files", "-z").stdout
    return sorted(f for f in out.split("\0") if f and os.path.basename(f) in TEMPORARY)


def added_in(path: str):
    """The commit that first added the file, or None when the history does not say."""
    out = git("log", "--diff-filter=A", "--format=%H", "--", path, check=False).stdout.split()
    return out[-1] if out else None


def stale(base: str) -> list:
    """[(path, adding commit)] of the temporary files added at or before base."""
    if git("cat-file", "-e", f"{base}^{{commit}}", check=False).returncode != 0:
        return []
    found = []
    for path in tracked():
        commit = added_in(path)
        if commit and git("merge-base", "--is-ancestor", commit, base, check=False).returncode == 0:
            found.append((path, commit))
    return found


def main(argv: list) -> int:
    base = argv[0] if argv and argv[0] else "HEAD"
    for path, commit in stale(base):
        name = os.path.basename(path)
        print(f"::warning file={path},line=1,title=Temporary file::{name} was added in {commit[:7]}, "
              f"an earlier pull request than this one. Delete it after its apply: {os.path.dirname(path)}/{name}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
