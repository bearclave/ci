#!/usr/bin/env python3
"""Fails when a moved unit plans as if its state had not come with it.

Usage: check-moves.py <selection.json> <plan-json-dir>

<selection.json> is the output of select-units.py; for every [old, new] in
its `moved`, the plan of the new path (<plan-json-dir>/<new>/tfplan.json)
may not create, destroy or replace a managed resource. Imports, moves,
updates and forgets are fine. A create means the state was not copied or
moved before the merge, and the apply would build everything a second time
next to the old resources. A unit without a JSON plan here is planned by
another job and skipped.
"""
import json
import os
import sys


def offending(plan: dict) -> list:
    """(address, actions) of the managed resources the plan creates or deletes."""
    out = []
    for rc in plan.get("resource_changes") or []:
        if rc.get("mode") != "managed":
            continue
        actions = (rc.get("change") or {}).get("actions") or []
        if "create" in actions or "delete" in actions:
            out.append((rc.get("address", "?"), "/".join(actions)))
    return out


def check(selection: dict, json_dir: str) -> list:
    """Error messages, one per offending resource."""
    errors = []
    for old, new in selection.get("moved") or []:
        # Moved into infrastructure/ with the same key: the state never moved,
        # so a create there is an ordinary change, not a missing state.
        if old.removeprefix("infrastructure/") == new.removeprefix("infrastructure/"):
            continue
        path = os.path.join(json_dir, new, "tfplan.json")
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as fh:
            plan = json.load(fh)
        for address, actions in offending(plan):
            errors.append(f"{new} (moved from {old}): {address} would {actions}. "
                          "Move its state before the merge (.github/state-moves.yaml).")
    return errors


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    with open(sys.argv[1], encoding="utf-8") as fh:
        found = check(json.load(fh), sys.argv[2])
    for message in found:
        print(f"::error title=Moved unit::{message}")
    sys.exit(1 if found else 0)
