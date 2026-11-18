#!/usr/bin/env python3
"""Fails when a principal this change creates is referenced by display name.

`dependencies` only orders the run. A plan creates nothing, so a lookup by
display name goes to Entra and finds nothing for a group or service principal
that the same change is still only planning. The reference has to go through
a dependency output as "id:${dependency...}" instead.
"""
import pathlib
import re
import subprocess
import sys

BASE = sys.argv[1] if len(sys.argv) > 1 else "origin/main"

EMAIL = re.compile(r'"([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+)"')

# Guests are users too; a new one referenced by address fails the same way.
SOURCES = [
    ("group", "tenant/groups/terragrunt.hcl", re.compile(r'"(sg-[A-Za-z0-9._-]+)"\s*=')),
    ("sp", "tenant/applications/terragrunt.hcl", re.compile(r'"(sp-[A-Za-z0-9._-]+)"\s*=')),
    ("user", "tenant/users/terragrunt.hcl", EMAIL),
    ("user", "tenant/external-users/terragrunt.hcl", EMAIL),
]
REFERENCE = re.compile(r'"(group|sp|user):([^"$]+)"')


def added(path, pattern):
    """Names present now and absent at the merge base.

    The comparison is between two whole files rather than over the diff: a
    changed description touches the line a name sits on, which would otherwise
    read as a new name.
    """
    merge_base = subprocess.run(
        ["git", "merge-base", BASE, "HEAD"],
        capture_output=True, text=True, check=False,
    ).stdout.strip() or BASE
    before = subprocess.run(
        ["git", "show", f"{merge_base}:{path}"],
        capture_output=True, text=True, check=False,
    ).stdout
    now = pathlib.Path(path).read_text(encoding="utf-8")
    return set(pattern.findall(now)) - set(pattern.findall(before))


references = {}
for unit in pathlib.Path(".").rglob("terragrunt.hcl"):
    if ".terragrunt-cache" in unit.parts:
        continue
    for kind, name in REFERENCE.findall(unit.read_text(encoding="utf-8")):
        references.setdefault((kind, name), set()).add(str(unit))

problems = []
for kind, path, pattern in SOURCES:
    for name in added(path, pattern):
        where = references.get((kind, name))
        if where:
            problems.append((f"{kind}:{name}", sorted(where)))

if problems:
    print("Principals created by this change and referenced by name:\n", file=sys.stderr)
    for reference, units in problems:
        print(f"  {reference}", file=sys.stderr)
        for unit in units:
            print(f"      {unit}", file=sys.stderr)
    print(
        '\nA plan creates nothing, so the lookup fails before they exist.\n'
        'Include the dependency and use "group-id:${dependency...}" for a group\n'
        'or "id:${dependency...}" for a service principal - a bare GUID carries\n'
        'no type, so the prefix has to.',
        file=sys.stderr,
    )
    sys.exit(1)

print("principal references OK")
