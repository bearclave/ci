#!/usr/bin/env python3
"""Works out from git which units a change touches.

Usage: select-units.py <base> [--no-expand] [--moves=<state-moves.json>]

Compares <base> with HEAD and prints one JSON object:

  all      true when the whole tree has to run
  units    the units to run when not the whole tree, with everything they
           depend on; --no-expand leaves the dependencies out
  deleted  units whose directory is gone: they only exist in <base> and are
           planned, and applied, as a destroy from there
  moved    [old, new] pairs of a unit directory whose state went with it:
           its state key (the path without "infrastructure/") is unchanged,
           or .github/state-moves.yaml lists the move. The new path is
           planned, the old one is not destroyed, and the plan of the new
           path may create and destroy nothing (check-moves.py)
  renamed  [old, new] pairs of a unit directory that moved without its
           state; the workflow refuses them
  excluded units of HEAD whose exclude block keeps them out of a plan (a
           device not under Terraform yet): terragrunt skips them, the lock
           file check too
  base     the commit compared against, or null when there was none to use

<state-moves.json> is .github/state-moves.yaml as JSON (yq):
{"moves": [{"from": <state key>, "to": <state key>}]}. A key is the unit
path with the "infrastructure/" prefix trimmed. `to` may be a new unit (the
owner copied the state blob) or an existing one (the owner moved the
resources into its state). An entry whose `from` is not a unit gone in this
change is ignored (it is stale once merged); one whose `to` is no unit fails.

What decides the selection:

- root.hcl and account.hcl touch every unit: the whole tree runs.
- A file under _common/ selects the units that include or read it, as
  `terragrunt find --reading --include` reports them; one that no unit
  reports (a template, a script, a deleted file), or a tree terragrunt
  cannot read, runs the whole tree.
- A context file (CONTEXT) applies to the unit in its own directory, every
  unit below it, and any other unit that reads it.
- Any other file belongs to the nearest unit above it. A file of a deleted
  unit belongs to nobody - that unit runs as a destroy.
- A file outside every unit (the workflows, the scripts) changes how the tree
  is planned, so when nothing else is touched the whole tree runs - except
  what never reaches a plan (the same as changes.py): .claude/, Markdown,
  and .github/ but its workflows, tool-versions.env and trivyignore.yaml.
- No usable <base> (unknown, not an ancestor of HEAD) or no difference at all
  means the whole tree.

What stays out are the units that DEPEND on a changed one: if the groups unit
changes and memberships stands on its outputs, this selection will not run
memberships. That is what the weekly drift job over the whole tree is for.
"""
import json
import os
import subprocess
import sys

CONTEXT = {"mg.hcl", "subscription.hcl", "region.hcl", "rg.hcl", "device.hcl"}
UNIT = "terragrunt.hcl"
CONTENT = "infrastructure/"
# What under .github/ changes a plan (changes.py has the same list).
PLAN_INPUTS = (".github/workflows/", ".github/tool-versions.env", ".github/trivyignore.yaml")


def no_plan_impact(path: str) -> bool:
    """A file outside every unit that cannot change how the tree is planned."""
    if path.startswith(".claude/") or path.lower().endswith(".md"):
        return True
    return path.startswith(".github/") and not path.startswith(PLAN_INPUTS)


def key(unit: str) -> str:
    """The state key of a unit: its path without the content folder."""
    return unit[len(CONTENT):] if unit.startswith(CONTENT) else unit


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=check)


def unit_dirs(ref: str) -> set:
    """Every directory that holds a terragrunt.hcl at the given commit."""
    files = git("ls-tree", "-r", "--name-only", ref).stdout.splitlines()
    return {f[: -len(UNIT) - 1] for f in files if f.endswith("/" + UNIT)}


def owner(path: str, units: set) -> str:
    """The nearest unit directory above the path, or None."""
    d = os.path.dirname(path)
    while d:
        if d in units:
            return d
        d = os.path.dirname(d)
    return None


class Graph:
    """The units of HEAD as terragrunt sees them, asked for once and only if needed."""

    def __init__(self):
        self._units = None

    def units(self) -> list:
        if self._units is None:
            out = subprocess.run(
                ["terragrunt", "find", "--json", "--dependencies", "--reading", "--include", "--exclude"],
                capture_output=True, text=True, check=True).stdout
            found = json.loads(out or "[]")
            found = found if isinstance(found, list) else found.get("units", [])
            self._units = [u for u in found if u.get("type") == "unit"]
        return self._units

    def excluded(self) -> list:
        """Units an exclude block keeps out of a plan."""
        out = []
        for u in self.units():
            ex = u.get("exclude") or {}
            if ex.get("if") is True and {"all", "plan"} & set(ex.get("actions") or []):
                out.append(u["path"])
        return sorted(out)

    def readers(self, path: str) -> set:
        """Units that include or read the file."""
        out = set()
        for u in self.units():
            files = list(u.get("reading") or []) + list((u.get("include") or {}).values())
            if any(os.path.normpath(os.path.relpath(f) if os.path.isabs(f) else f) == path
                   for f in files):
                out.add(u["path"])
        return out

    def expand(self, selected: list) -> list:
        """The units plus everything they depend on, transitively.

        --queue-include-dir runs exactly the directories it is handed;
        terragrunt does not pull a unit's dependencies into the queue, and a
        key vault came to be created before its resource group that way.
        """
        deps = {u["path"]: u.get("dependencies") or [] for u in self.units()}
        closure, queue = set(), list(selected)
        while queue:
            unit = queue.pop()
            if unit not in closure:
                closure.add(unit)
                queue.extend(deps.get(unit, []))
        return sorted(closure)


def read_moves(path: str) -> dict:
    """{from key: to key} from the JSON form of .github/state-moves.yaml."""
    if not path or not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh) or {}
    moves, problems = {}, []
    entries = data.get("moves") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        entries, problems = [], ["moves: a list of {from, to} is expected"] if data else []
    for k, e in enumerate(entries, 1):
        src, dst = (e or {}).get("from"), (e or {}).get("to")
        if not (isinstance(src, str) and src and isinstance(dst, str) and dst):
            problems.append(f"entry {k}: from and to must both be state keys")
        elif src.startswith(CONTENT) or dst.startswith(CONTENT) or src.endswith(".tfstate") \
                or dst.endswith(".tfstate"):
            problems.append(f"entry {k}: a state key has no {CONTENT} prefix and no file name")
        elif src == dst:
            problems.append(f"entry {k}: from and to are the same")
        elif src in moves:
            problems.append(f"entry {k}: {src} is moved twice")
        else:
            moves[src.strip("/")] = dst.strip("/")
    if problems:
        sys.exit("state-moves.yaml: " + "; ".join(problems))
    return moves


def select(base: str, do_expand: bool = True, moves_file: str = "", graph=None) -> dict:
    graph = graph or Graph()
    result = {"all": False, "units": [], "deleted": [], "moved": [], "renamed": [], "excluded": [],
              "base": None}
    moves = read_moves(moves_file)
    try:
        result["excluded"] = graph.excluded()
    except (OSError, subprocess.CalledProcessError, ValueError):
        pass

    usable = (base and git("cat-file", "-e", f"{base}^{{commit}}", check=False).returncode == 0
              and git("merge-base", "--is-ancestor", base, "HEAD", check=False).returncode == 0)
    if not usable:
        result["all"] = True
        return result
    result["base"] = git("rev-parse", f"{base}^{{commit}}").stdout.strip()

    head_units = unit_dirs("HEAD")
    base_units = unit_dirs(base)
    gone = base_units - head_units
    by_key = {key(u): u for u in head_units}

    changes = []
    for line in git("diff", "--name-status", "-M", base, "HEAD").stdout.splitlines():
        status, *paths = line.split("\t")
        changes.append((status[0], paths))
    if not changes:
        result["all"] = True
        return result

    # A unit whose state went with it: the same key, or a listed move.
    selected, moved_from = set(), set()
    for old in sorted(gone):
        target = moves.get(key(old), key(old))
        new = by_key.get(target)
        if key(old) in moves and not new:
            sys.exit(f"state-moves.yaml: {key(old)} moves to {target}, which is no unit")
        if new and new != old and (target != key(old) or new not in base_units):
            result["moved"].append([old, new])
            moved_from.add(old)
            selected.add(new)

    shared, outside = set(), False
    for status, paths in changes:
        if status == "R":
            old, new = paths
            if old.endswith("/" + UNIT) and new.endswith("/" + UNIT):
                old_dir, new_dir = os.path.dirname(old), os.path.dirname(new)
                if old_dir in gone - moved_from and new_dir not in base_units:
                    result["renamed"].append([old_dir, new_dir])
        for path in paths:
            if path in ("root.hcl", "account.hcl"):
                result["all"] = True
                continue
            if path.startswith("_common/"):
                shared.add(path)
                continue
            if os.path.basename(path) in CONTEXT:
                here = os.path.dirname(path)
                selected |= {u for u in head_units if u == here or u.startswith(here + "/")}
                shared.add(path)
                continue
            if path.endswith("/" + UNIT) and os.path.dirname(path) in gone:
                continue
            unit = owner(path, head_units | gone)
            if unit in head_units:
                selected.add(unit)
            elif unit is None and not no_plan_impact(path):
                outside = True

    renamed_old = {old for old, _ in result["renamed"]}
    result["deleted"] = sorted(gone - renamed_old - moved_from)
    if result["all"]:
        return result
    for path in sorted(shared):
        common = path.startswith("_common/")
        if common and not os.path.isfile(path):
            result["all"] = True
            return result
        try:
            readers = graph.readers(path)
        except (OSError, subprocess.CalledProcessError, ValueError):
            if not common:
                continue
            readers = set()
        if common and not readers:
            result["all"] = True
            return result
        selected |= readers & head_units
    if not selected:
        # Only the workflows or the scripts changed (they change how
        # everything is planned), only what never reaches a plan, or only
        # units were deleted.
        result["all"] = outside
        return result
    if selected >= head_units:
        result["all"] = True
        return result
    result["units"] = graph.expand(sorted(selected)) if do_expand else sorted(selected)
    return result


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    moves_arg = next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--moves=")), "")
    print(json.dumps(select(args[0] if args else "", "--no-expand" not in sys.argv, moves_arg)))
