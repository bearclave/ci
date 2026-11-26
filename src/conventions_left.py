"""The rules of what a unit and a repository leave behind (code/terragrunt.md of governance).

collection-unit, imports-left, moves-left and state-moves-stale. They live in
a file of their own because conventions.py travels inside a workflow step and
actionlint cannot take a script of about 64 KB; the workflow writes this file
next to it and conventions.py imports it when one of these rules runs. Each
rule takes the conventions module as its first argument, for its parser and
helpers.
"""
import os
import re
import subprocess
import time


def is_empty_inputs(attr):
    return [t.value for t in attr.value] == ["{", "}"]


def rule_collection_unit(c, repo, params, units, report):
    """A collection unit is the two includes and `inputs = {}`, nothing else; no other unit has an empty inputs.

    A collection unit is a unit whose included _common/units file reads definitions/ (fileset, or file for a settings.yaml), with or without objects yet. The comment above
    the empty inputs is unit-block-order's.
    """
    for path in c.shape_units(repo, units):
        root, _, _ = repo.hcl(path)
        # A collection unit is one whose _common/units file reads definitions/.
        collection = any("definitions/" in (c.text_of(repo, f) or "")
                         for f in c.unit_includes(repo, path) if repo.exists(f))
        inputs = root.attrs.get("inputs")
        if not collection:
            if inputs is not None and is_empty_inputs(inputs):
                report.add("collection-unit", path, inputs.line,
                           "inputs = {} in a unit whose _common/units file does not read definitions/: only a collection unit has an empty "
                           "inputs; list the instance settings")
            continue
        includes = [b.labels[0] if b.labels else "" for b in root.find("include")]
        if sorted(includes) != ["root", "unit"]:
            report.add("collection-unit", path, (root.find("include") or [root])[0].line,
                       f"a collection unit has include \"root\" and include \"unit\", it has: "
                       f"{', '.join(includes) or 'none'}")
        for b in root.blocks:
            if b.kind != "include":
                report.add("collection-unit", path, b.line,
                           f"a {b.kind} block in a collection unit: it is the two includes and inputs = {{}}; "
                           "what every object shares goes into _common/units/<module>.hcl")
        for name, a in root.attrs.items():
            if name != "inputs":
                report.add("collection-unit", path, a.line,
                           f"{name} in a collection unit: it is the two includes and inputs = {{}}")
        if inputs is None:
            report.add("collection-unit", path, 1, "a collection unit has `inputs = {}` (with its Content comment)")
        elif not is_empty_inputs(inputs):
            report.add("collection-unit", path, inputs.line,
                       "a collection unit has inputs = {}: the objects are the YAML files in definitions/, "
                       "a value they share goes into _common/units/<module>.hcl")


# --------------------------------------------------------------------------
# What is left behind: imports.hcl, moves.hcl, removed.hcl and state moves

RE_INSTANCE_KEY = re.compile(r'\[\\?"(.+?)\\?"\]')


def added_at(repo, path):
    """Unix time of the commit that first added the path, None when the history does not say."""
    out = subprocess.run(["git", "log", "--diff-filter=A", "--format=%ct", "--", path], cwd=repo.root,
                         capture_output=True, text=True).stdout.split()
    return int(out[-1]) if out else None


def older_than(repo, path, days):
    """True when the path was added `days` or more days ago (never, when the history is cut)."""
    at = added_at(repo, path)
    return at is not None and time.time() - at >= days * 86400


def left_behind(c, repo, params, report, rule, names):
    days = int(params.get("days", 7))
    for path in repo.files:
        name = os.path.basename(path)
        if name not in names:
            continue
        if older_than(repo, path, days):
            report.add(rule, path, 1,
                       f"{name} was added more than {days} days ago: delete it (and its generate block and "
                       "template when it is the last one) in the first pull request after the apply")
        if name == "imports.hcl":
            import_without_configuration(c, repo, path, rule, report)


def import_without_configuration(c, repo, path, rule, report):
    """An import of a collection object whose definitions/<key>.yaml is gone fails the plan."""
    unit = os.path.dirname(path)
    keys = {os.path.basename(f)[:-len(".yaml")] for f in repo.files
            if f.startswith(unit + "/definitions/") and f.endswith(".yaml")}
    if not keys:
        return
    root, _, _ = repo.hcl(path)
    attr = root.attrs.get("imports")
    imports = c.literal(attr.value) if attr else None
    if not isinstance(imports, dict):
        return
    for address in imports:
        m = RE_INSTANCE_KEY.search(address)
        if m and m.group(1) not in keys:
            report.add(rule, path, attr.line,
                       f"{address} imports {m.group(1)!r}, which has no definitions/{m.group(1)}.yaml: "
                       "remove the line with the object (Configuration for import target does not exist)",
                       level="error")


def rule_imports_left(c, repo, params, units, report):
    """imports.hcl older than 7 days warns; an import of an object without its YAML file fails."""
    left_behind(c, repo, params, report, "imports-left", ("imports.hcl",))


def rule_moves_left(c, repo, params, units, report):
    """moves.hcl and removed.hcl older than 7 days warn."""
    left_behind(c, repo, params, report, "moves-left", ("moves.hcl", "removed.hcl"))


def state_moves(c, repo, path):
    """[(line, from, to)] of .github/state-moves.yaml (a list of from/to pairs)."""
    entries, current = [], None
    for n, raw in enumerate(c.text_lines(repo, path) or [], 1):
        line = raw.split("#", 1)[0].rstrip()
        m = re.match(r"^\s*(-\s+)?(from|to)\s*:\s*[\"']?([^\"']+?)[\"']?\s*$", line)
        if not m:
            continue
        if m.group(1) or current is None:
            current = {"line": n}
            entries.append(current)
        current[m.group(2)] = m.group(3)
    return [(e["line"], e.get("from"), e.get("to")) for e in entries if e.get("from") and e.get("to")]


def rule_state_moves_stale(c, repo, params, units, report):
    """An entry of .github/state-moves.yaml whose `to` is a unit, whose `from` is gone and that was added a week ago.

    Whether the apply happened is not in the code: a move whose new unit is
    there, the old one gone and the entry older than `days` (default 7) is
    applied, and the first pull request after it removes the entry.
    """
    path = params.get("file", ".github/state-moves.yaml")
    if not repo.exists(path):
        return
    base = params.get("root", "infrastructure")
    days = int(params.get("days", 7))
    for line, src, dst in state_moves(c, repo, path):
        exists = repo.exists(f"{base}/{dst.strip('/')}/terragrunt.hcl")
        gone = not any(f.startswith(f"{base}/{src.strip('/')}/") for f in repo.files)
        if not (exists and gone):
            continue
        out = subprocess.run(["git", "log", "-S" + src, "--reverse", "--format=%ct", "--", path],
                             cwd=repo.root, capture_output=True, text=True).stdout.split()
        if out and time.time() - int(out[0]) >= days * 86400:
            report.add("state-moves-stale", path, line,
                       f"{src} -> {dst}: the unit is at its new path, the old one is gone and the entry is "
                       f"older than {days} days; remove it (and the old state blob) in this pull request")
