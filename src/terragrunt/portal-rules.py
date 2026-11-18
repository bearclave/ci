#!/usr/bin/env python3
"""Analytic rules in the workspace that the code does not manage.

Usage: portal-rules.py <plan-json-dir> <unit>

Reads the plan of the analytic rules unit (<plan-json-dir>/<unit>/tfplan.json)
for the rules the code manages and the workspace they live in, lists the
rules the workspace really has (az rest, with the session the job already
signed in with) and reports every one the code does not know: a rule created
in the portal, or one left behind. Each becomes a warning and a row in the
job summary; the exit code stays 0, this is a report.

Terraform's drift plan cannot see these: it only looks at what it manages.
"""
import json
import os
import subprocess
import sys

API = "2024-09-01"


def managed(plan: dict):
    """(workspace id, names of the alertRules) from the planned values."""
    names, workspace = set(), None
    stack = [(plan.get("planned_values") or {}).get("root_module") or {}]
    while stack:
        module = stack.pop()
        stack += module.get("child_modules") or []
        for r in module.get("resources") or []:
            values = r.get("values") or {}
            if r.get("type") == "azapi_resource" and \
                    str(values.get("type", "")).startswith("Microsoft.SecurityInsights/alertRules@"):
                names.add(values.get("name"))
                workspace = workspace or values.get("parent_id")
    return workspace, names


# Rules Sentinel creates and keeps by itself; they do not belong in the code.
BUILT_IN_KINDS = {"Fusion"}


def unmanaged(rules: list, names: set) -> list:
    """(name, kind, display name, enabled) of the rules not in names."""
    out = []
    for r in rules:
        if r.get("name") in names or r.get("kind") in BUILT_IN_KINDS:
            continue
        p = r.get("properties") or {}
        out.append((r.get("name"), r.get("kind", ""), p.get("displayName", ""), p.get("enabled")))
    return sorted(out, key=lambda x: (x[2] or "", x[0]))


def workspace_rules(workspace: str) -> list:
    url = f"https://management.azure.com{workspace}/providers/Microsoft.SecurityInsights/alertRules?api-version={API}"
    rules = []
    while url:
        page = json.loads(subprocess.run(["az", "rest", "--method", "get", "--url", url],
                                         capture_output=True, text=True, check=True).stdout)
        rules += page.get("value") or []
        url = page.get("nextLink")
    return rules


def main(plan_dir: str, unit: str) -> int:
    path = os.path.join(plan_dir, unit, "tfplan.json")
    if not os.path.isfile(path):
        print(f"::warning::no plan of {unit}, portal rules not checked")
        return 0
    with open(path, encoding="utf-8") as fh:
        workspace, names = managed(json.load(fh))
    if not workspace:
        print(f"::warning::{unit} manages no analytic rule, portal rules not checked")
        return 0
    found = unmanaged(workspace_rules(workspace), names)
    lines = ["## Analytic rules outside the code", ""]
    if not found:
        lines.append(f"None: the workspace has exactly the {len(names)} rules the code manages.")
    else:
        lines += [f"{len(found)} rules in the workspace that the code does not manage. "
                  "Put each into the code, or delete it in the portal.", "",
                  "| Display name | Kind | Enabled | Name |", "|---|---|---|---|"]
        for name, kind, display, enabled in found:
            lines.append(f"| {display} | {kind} | {enabled} | `{name}` |")
            print(f"::warning title=portal rule::{display or name} ({kind}) is not in the code")
    text = "\n".join(lines) + "\n"
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(text)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:3]))
