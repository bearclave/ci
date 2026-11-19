#!/usr/bin/env python3
"""Decides whether a pull request's changed files can change a plan.

Usage: changes.py <relevant-regex> <changed-files> [<unit-files>]

<changed-files> lists the paths of the pull request, one per line.
<unit-files> lists the terragrunt.hcl files of its head commit; it is read
only when a Markdown file changed, and a missing or empty one makes every
Markdown file count.

Prints `relevant=true` or `relevant=false`. A path counts when it matches
<relevant-regex> (grep -E syntax that Python reads the same way, e.g.
'^(tenant/|_common/|\\.github/)|^(root|account)\\.hcl$') and is not one of
the paths that never reach a plan:

- .github/dependabot.yml and .github/release.yml: read by GitHub, not by
  the pipeline;
- anything under .claude/: the agents' rules;
- a Markdown file outside every unit (no terragrunt.hcl in its directory or
  above): a unit may read a file of its own, nothing reads a README of
  _common/ or .github/.
"""
import os
import re
import sys

NEVER = {".github/dependabot.yml", ".github/release.yml"}
UNIT = "terragrunt.hcl"


def unit_dirs(unit_files: list) -> set:
    return {os.path.dirname(f) for f in unit_files if os.path.basename(f) == UNIT}


def in_unit(path: str, units: set) -> bool:
    d = os.path.dirname(path)
    while d:
        if d in units:
            return True
        d = os.path.dirname(d)
    return False


def ignored(path: str, units) -> bool:
    """True for a path that cannot change a plan. units None: not known."""
    if path in NEVER or path.startswith(".claude/"):
        return True
    if path.lower().endswith(".md"):
        return units is not None and not in_unit(path, units)
    return False


def relevant(regex: str, files: list, unit_files=None) -> bool:
    units = unit_dirs(unit_files) if unit_files else None
    pattern = re.compile(regex)
    return any(pattern.search(f) and not ignored(f, units) for f in files if f)


def read(path: str) -> list:
    try:
        with open(path, encoding="utf-8") as f:
            return [line.strip() for line in f if line.strip()]
    except OSError:
        return []


if __name__ == "__main__":
    if len(sys.argv) not in (3, 4):
        sys.exit(__doc__)
    unit_files = read(sys.argv[3]) if len(sys.argv) == 4 else None
    print(f"relevant={'true' if relevant(sys.argv[1], read(sys.argv[2]), unit_files) else 'false'}")
