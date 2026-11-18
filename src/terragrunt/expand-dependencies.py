#!/usr/bin/env python3
"""Expands a set of units with everything they depend on, transitively.

`--queue-include-dir` runs exactly the directories it is handed; terragrunt
does not pull a unit's dependencies into the queue by itself. A change that
touches only a child therefore runs against a parent that may not exist yet,
which is how a key vault came to be created before its resource group.

Reads unit paths on stdin, writes the closure on stdout.
"""
import json
import subprocess
import sys

selected = {line.strip() for line in sys.stdin if line.strip()}
if not selected:
    sys.exit(0)

found = json.loads(subprocess.run(
    ["terragrunt", "find", "--dependencies", "--json"],
    capture_output=True, text=True, check=True,
).stdout)
units = found if isinstance(found, list) else found.get("units", [])
dependencies = {
    u["path"]: u.get("dependencies") or []
    for u in units if u.get("type") == "unit"
}

closure, queue = set(), list(selected)
while queue:
    unit = queue.pop()
    if unit in closure:
        continue
    closure.add(unit)
    queue.extend(dependencies.get(unit, []))

for unit in sorted(closure):
    print(unit)
