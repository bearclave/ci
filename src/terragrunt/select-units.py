#!/usr/bin/env python3
"""Works out from git which units a change touches.

Usage: select-units.py <base> [--no-expand]

Compares <base> with HEAD and prints one JSON object:

  all      true when the whole tree has to run
  units    the units to run when not the whole tree, with everything they
           depend on (see expand-dependencies.py); --no-expand leaves that out
  deleted  units whose directory is gone: they only exist in <base> and are
           planned, and applied, as a destroy from there
  renamed  [old, new] pairs of a unit directory that moved. The state key is
           the unit's path, so a move is a new unit plus a deleted one; the
           workflow refuses it
  base     the commit compared against, or null when there was none to use

What decides the selection:

- A change to root.hcl, account.hcl or anything under _common/ touches every
  unit: the whole tree runs.
- A context file (mg.hcl, subscription.hcl, region.hcl, rg.hcl) applies to
  the unit in its own directory and every unit below it.
- Any other file belongs to the nearest unit above it. A file of a deleted
  unit belongs to nobody - that unit runs as a destroy.
- A file outside every unit (the workflows, the scripts) changes how the tree
  is planned, so when nothing else is touched the whole tree runs.
- No usable <base> (unknown, not an ancestor of HEAD) or no difference at all
  means the whole tree.

What stays out are the units that DEPEND on a changed one: if the groups unit
changes and memberships stands on its outputs, this selection will not run
memberships. That is what the weekly drift job over the whole tree is for.
"""
import json
import os
import pathlib
import subprocess
import sys

CONTEXT = {"mg.hcl", "subscription.hcl", "region.hcl", "rg.hcl"}
UNIT = "terragrunt.hcl"


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=check)


def unit_dirs(ref: str) -> set:
    """Every directory that holds a terragrunt.hcl at the given commit."""
    files = git("ls-tree", "-r", "--name-only", ref).stdout.splitlines()
    return {f[: -len(UNIT) - 1] for f in files if f.endswith("/" + UNIT)}


def is_global(path: str) -> bool:
    return path in ("root.hcl", "account.hcl") or path.startswith("_common/")


def owner(path: str, units: set) -> str:
    """The nearest unit directory above the path, or None."""
    d = os.path.dirname(path)
    while d:
        if d in units:
            return d
        d = os.path.dirname(d)
    return None


def expand(units: list) -> list:
    """The units plus everything they depend on, via expand-dependencies.py."""
    script = pathlib.Path(__file__).with_name("expand-dependencies.py")
    out = subprocess.run([sys.executable, str(script)], input="\n".join(units),
                         capture_output=True, text=True, check=True).stdout
    return sorted({line.strip() for line in out.splitlines() if line.strip()})


def select(base: str, do_expand: bool = True) -> dict:
    result = {"all": False, "units": [], "deleted": [], "renamed": [], "base": None}

    usable = (base and git("cat-file", "-e", f"{base}^{{commit}}", check=False).returncode == 0
              and git("merge-base", "--is-ancestor", base, "HEAD", check=False).returncode == 0)
    if not usable:
        result["all"] = True
        return result
    result["base"] = git("rev-parse", f"{base}^{{commit}}").stdout.strip()

    head_units = unit_dirs("HEAD")
    base_units = unit_dirs(base)
    gone = base_units - head_units

    changes = []
    for line in git("diff", "--name-status", "-M", base, "HEAD").stdout.splitlines():
        status, *paths = line.split("\t")
        changes.append((status[0], paths))
    if not changes:
        result["all"] = True
        return result

    selected, outside = set(), False
    for status, paths in changes:
        if status == "R":
            old, new = paths
            if old.endswith("/" + UNIT) and new.endswith("/" + UNIT):
                old_dir, new_dir = os.path.dirname(old), os.path.dirname(new)
                if old_dir in gone and new_dir not in base_units:
                    result["renamed"].append([old_dir, new_dir])
                    continue
        for path in paths:
            if is_global(path):
                result["all"] = True
            if os.path.basename(path) in CONTEXT:
                here = os.path.dirname(path)
                selected |= {u for u in head_units if u == here or u.startswith(here + "/")}
                continue
            if path.endswith("/" + UNIT) and os.path.dirname(path) in gone:
                continue
            unit = owner(path, head_units | gone)
            if unit in head_units:
                selected.add(unit)
            elif unit is None:
                outside = True

    renamed_old = {old for old, _ in result["renamed"]}
    result["deleted"] = sorted(gone - renamed_old)
    if result["all"]:
        return result
    if not selected:
        # Only the workflows, the scripts or the docs changed, or only units
        # were deleted. The first changes how everything is planned.
        result["all"] = outside
        return result
    result["units"] = expand(sorted(selected)) if do_expand else sorted(selected)
    return result


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    print(json.dumps(select(args[0] if args else "", "--no-expand" not in sys.argv)))
