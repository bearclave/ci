#!/usr/bin/env python3
"""Verifies an apply: what the second plan, the Activity Log and the check
blocks say about it.

Usage:
  verify-apply.py subscriptions <json-dir>
      The subscriptions of the azurerm providers of the planned units, one
      per line (none where no unit has an azurerm provider).
  verify-apply.py checks <json-dir> [--log <file>]
      The check blocks that failed, from the plans in <json-dir> and the
      warnings in a terraform log; exit 3 when one failed.
  verify-apply.py report --replan-rc <n|skipped> --json-dir <dir>
                         --replan-log <file> --apply-log <file>
                         --activity <file> --out <verification.json>
      Writes verification.json and prints the Markdown of the apply comment's
      ### Verification section (without the heading).

The second plan runs right after the apply, over the units it applied, with
-detailed-exitcode: 0 means the code and the real state agree, 2 that a
change is still proposed (the apply did not converge, or something changed
it at once), anything else that the plan failed.

<activity> is what the workflow read from the Activity Log:
{"subscriptions": [...], "start": ..., "end": ..., "events": [...],
 "errors": [...]} or {"skipped": "<reason>"}.

verification.json (version 1):
  result        passed | failed
  replan        result (no-changes | changes | error | skipped), exit_code,
                units_with_changes, errors
  activity_log  result (passed | failed | skipped | unreadable), reason,
                subscriptions, window {start, end}, failures [{time,
                operation, resource, status, sub_status, message}]
  checks        result (passed | failed | none), failed [{unit, check,
                message, source}], passed, unknown
"""
import argparse
import json
import os
import pathlib
import re
import sys

RE_LINE = re.compile(r"\[(?P<unit>[^\]]+)\] (?:terraform|tofu): ?(?P<text>.*)$")
RE_CHECK = re.compile(r'in check "(?P<name>[^"]+)"')
NOOP = ({"no-op"}, {"read"})
MAX_ERRORS = 20
# A unit is reported by its state key, the path without infrastructure/.
CONTENT = "infrastructure/"


def state_key(unit: str) -> str:
    return unit[len(CONTENT):] if unit.startswith(CONTENT) else unit


def plans(json_dir: str):
    """(unit, plan) for every tfplan.json below the directory."""
    root = pathlib.Path(json_dir)
    if not root.is_dir():
        return
    for f in sorted(root.rglob("tfplan.json")):
        try:
            plan = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        yield state_key(str(f.parent.relative_to(root))), plan


def subscriptions(json_dir: str) -> list:
    found = []
    for _, plan in plans(json_dir):
        configs = (plan.get("configuration") or {}).get("provider_config") or {}
        for key, cfg in configs.items():
            if cfg.get("name") != "azurerm" and not key.split(".")[0] == "azurerm":
                continue
            sub = ((cfg.get("expressions") or {}).get("subscription_id") or {}).get("constant_value")
            if sub and sub not in found:
                found.append(sub)
    return found


def changed(plan: dict) -> bool:
    for rc in plan.get("resource_changes") or []:
        if set((rc.get("change") or {}).get("actions") or []) not in NOOP:
            return True
    for oc in (plan.get("output_changes") or {}).values():
        if set(oc.get("actions") or []) not in NOOP:
            return True
    return False


def plan_checks(json_dir: str) -> tuple:
    """Failed checks from the plans, and the count of passed and unknown."""
    failed, passed, unknown = [], 0, 0
    for unit, plan in plans(json_dir):
        for c in plan.get("checks") or []:
            if (c.get("address") or {}).get("kind") != "check":
                continue  # preconditions and postconditions show as errors
            name = (c.get("address") or {}).get("name") or (c.get("address") or {}).get("to_display")
            status = c.get("status")
            if status == "pass":
                passed += 1
            elif status == "fail":
                messages = [p.get("message", "") for i in c.get("instances") or []
                            for p in i.get("problems") or []]
                failed.append({"unit": unit, "check": name,
                               "message": "; ".join(m for m in messages if m) or "assertion failed",
                               "source": "plan"})
            else:
                unknown += 1
    return failed, passed, unknown


def log_checks(path: str) -> list:
    """Failed check blocks from the warnings in a terraform log.

    Warning: Check block assertion failed
      on main.tf line 7, in check "same":
       7:     condition = ...
        |  ...
    <message>
    """
    if not path or not os.path.exists(path):
        return []
    open_by_unit, failed = {}, []
    for raw in pathlib.Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        m = RE_LINE.search(raw)
        if not m:
            continue
        unit, text = state_key(m.group("unit")), m.group("text")
        if "Check block assertion failed" in text:
            open_by_unit[unit] = {"unit": unit, "check": None, "message": None, "source": "apply"}
            continue
        cur = open_by_unit.get(unit)
        if cur is None:
            continue
        name = RE_CHECK.search(text)
        if name and cur["check"] is None:
            cur["check"] = name.group("name")
            continue
        if text and not text[0].isspace():
            cur["message"] = text.strip()
            failed.append(cur)
            del open_by_unit[unit]
    for cur in open_by_unit.values():
        cur["message"] = cur["message"] or "assertion failed"
        failed.append(cur)
    return failed


def merged_checks(json_dir: str, log: str) -> tuple:
    from_plan, passed, unknown = plan_checks(json_dir)
    seen = {(c["unit"], c["check"]) for c in from_plan}
    failed = list(from_plan)
    for c in log_checks(log):
        if (c["unit"], c["check"]) not in seen:
            seen.add((c["unit"], c["check"]))
            failed.append(c)
    return failed, passed, unknown


def replan_errors(path: str) -> list:
    if not path or not os.path.exists(path):
        return []
    out = []
    for raw in pathlib.Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        m = RE_LINE.search(raw)
        if m and m.group("text").startswith("Error:"):
            out.append(f"{state_key(m.group('unit'))}: {m.group('text')[len('Error:'):].strip()}")
    return out[:MAX_ERRORS]


def activity_section(path: str) -> dict:
    try:
        data = json.loads(pathlib.Path(path).read_text(encoding="utf-8")) if path else {}
    except (OSError, ValueError):
        return {"result": "unreadable", "reason": "the Activity Log query left no result"}
    if "skipped" in data:
        return {"result": "skipped", "reason": data["skipped"]}
    failures = []
    for e in data.get("events") or []:
        props = e.get("properties") or {}
        message = props.get("statusMessage") or ""
        try:
            parsed = json.loads(message)
            err = parsed.get("error") or parsed
            message = err.get("message") or message
        except (ValueError, AttributeError):
            pass
        failures.append({
            "time": e.get("eventTimestamp"),
            "operation": (e.get("operationName") or {}).get("value"),
            "resource": e.get("resourceId"),
            "status": (e.get("status") or {}).get("value"),
            "sub_status": (e.get("subStatus") or {}).get("value"),
            "message": message[:500],
        })
    section = {
        "subscriptions": data.get("subscriptions") or [],
        "window": {"start": data.get("start"), "end": data.get("end")},
        "failures": failures,
    }
    if failures:
        section["result"] = "failed"
    elif data.get("errors"):
        # Not being able to read the log is not a failed apply.
        section["result"] = "unreadable"
        section["reason"] = "; ".join(data["errors"])[:500]
    else:
        section["result"] = "passed"
    return section


def report(args) -> dict:
    rc = args.replan_rc
    replan = {"exit_code": None, "units_with_changes": [], "errors": []}
    if rc == "skipped":
        replan["result"] = "skipped"
    else:
        replan["exit_code"] = int(rc)
        replan["units_with_changes"] = [u for u, p in plans(args.json_dir) if changed(p)]
        if replan["exit_code"] == 0:
            replan["result"] = "no-changes"
        elif replan["exit_code"] == 2:
            replan["result"] = "changes"
        else:
            replan["result"] = "error"
            replan["errors"] = replan_errors(args.replan_log)
        # run --all may report the highest code of one unit and hide another.
        if replan["result"] == "no-changes" and replan["units_with_changes"]:
            replan["result"] = "changes"

    failed, passed, unknown = merged_checks(args.json_dir, args.apply_log)
    checks = {"result": "failed" if failed else ("passed" if passed else "none"),
              "failed": failed, "passed": passed, "unknown": unknown}
    activity = activity_section(args.activity)

    bad = (replan["result"] in ("changes", "error")
           or activity["result"] == "failed"
           or checks["result"] == "failed")
    return {
        "version": 1,
        "result": "failed" if bad else "passed",
        "commit": os.environ.get("GITHUB_SHA"),
        "run_url": run_url(),
        "replan": replan,
        "activity_log": activity,
        "checks": checks,
    }


def run_url() -> str:
    server, repo, run = (os.environ.get(k) for k in ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID"))
    return f"{server}/{repo}/actions/runs/{run}" if server and repo and run else None


ICON = {"passed": "✅", "no-changes": "✅", "none": "✅", "skipped": "⏭️",
        "unreadable": "⚠️", "failed": "❌", "changes": "❌", "error": "❌"}


def markdown(v: dict) -> str:
    """The body of the apply comment's ### Verification section (the comment
    and the job summary add the heading): a | Check | Result | Detail | table."""
    mark = {"passed": "passed", "failed": "**failed**"}
    lines = [f"{ICON[v['result']]} The apply {mark[v['result']]} its verification. "
             "Nothing is rolled back by itself.", "",
             "| Check | Result | Detail |", "|---|---|---|"]
    r = v["replan"]
    detail = {
        "no-changes": "the second plan proposes nothing",
        "changes": "still proposes a change in " + (", ".join(f"`{u}`" for u in r["units_with_changes"]) or "a unit"),
        "error": "the second plan failed",
        "skipped": "no unit was applied",
    }[r["result"]]
    lines.append(f"| Plan again | {ICON[r['result']]} {r['result']} | {detail} |")
    a = v["activity_log"]
    if a["result"] == "skipped":
        detail = a["reason"]
    elif a["result"] == "unreadable":
        detail = a.get("reason", "")
    else:
        detail = (f"{len(a['failures'])} failed operation(s) of the apply identity in "
                  f"{', '.join(f'`{s}`' for s in a['subscriptions'])}")
    lines.append(f"| Azure Activity Log | {ICON[a['result']]} {a['result']} | {detail.replace('|', '/')} |")
    c = v["checks"]
    detail = (f"{len(c['failed'])} failed, {c['passed']} passed, {c['unknown']} unknown"
              if c["result"] != "none" else "no check block in the applied units")
    lines.append(f"| Check blocks | {ICON[c['result']]} {c['result']} | {detail} |")

    if r["errors"]:
        lines += ["", "Second plan errors:", ""] + [f"- {e}" for e in r["errors"]]
    if a.get("failures"):
        lines += ["", "Failed Azure operations:", ""]
        lines += [f"- {f['time']} `{f['operation']}` on `{f['resource']}`: {f['message'] or f['sub_status'] or f['status']}"
                  for f in a["failures"][:MAX_ERRORS]]
    if c["failed"]:
        lines += ["", "Failed check blocks:", ""]
        lines += [f"- `{f['unit']}` check `{f['check']}`: {f['message']}" for f in c["failed"]]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("subscriptions")
    s.add_argument("json_dir")
    c = sub.add_parser("checks")
    c.add_argument("json_dir")
    c.add_argument("--log", default="")
    r = sub.add_parser("report")
    r.add_argument("--replan-rc", required=True)
    r.add_argument("--json-dir", required=True)
    r.add_argument("--replan-log", default="")
    r.add_argument("--apply-log", default="")
    r.add_argument("--activity", default="")
    r.add_argument("--out", required=True)
    args = ap.parse_args()

    if args.cmd == "subscriptions":
        print("\n".join(subscriptions(args.json_dir)))
        return 0
    if args.cmd == "checks":
        failed, passed, unknown = merged_checks(args.json_dir, args.log)
        for f in failed:
            print(f"::error title=check block failed::{f['unit']}: check \"{f['check']}\": {f['message']}")
        print(f"Check blocks: {len(failed)} failed, {passed} passed, {unknown} unknown.")
        return 3 if failed else 0
    v = report(args)
    pathlib.Path(args.out).write_text(json.dumps(v, indent=2) + "\n", encoding="utf-8")
    sys.stdout.write(markdown(v))
    return 0


if __name__ == "__main__":
    sys.exit(main())
