#!/usr/bin/env python3
"""Turns the output of a terragrunt apply into the apply comment of the merged pull request.

Usage: apply-summary.py <apply output>... --changes=<changes.json> [options]

The comment, top to bottom:

  ## ✅ Apply complete            (## ❌ Apply failed when the apply job did not
                                   succeed or any resource failed)
  repo · merge commit · units · duration · run
  icon | Resource | Name | What happened | Result
  imports that changed nothing, as one count line
  ### ❌ Errors                   when there are any
  ### Verification               what the post-apply checks reported

What was meant to happen comes from changes.json, the entries the plan job of
the same run wrote (plan-summary.py --changes-out): the apply runs the plan's
selection, so the rows are the rows of the plan comment, in the past tense.
What did happen comes from the apply output, per resource: a "... complete"
line is done, an error "with <address>" is failed. A unit that finished
("Apply complete!") has applied everything it planned, moves and imports
included; a unit that failed leaves the rest of its resources not applied; a
unit that never started is not run.

The Verification section is filled from --verification=<dir>: every *.md file
in it, in name order, as it is. A post-apply check writes its result there as
markdown (a table | Check | Result | Detail | is the convention, see the
README) and uploads it as an artifact named verification-<run id>[-<name>];
the report job downloads all of them before it renders. Without any, the
section says that no check has reported.

Options:
  --changes=<f>       changes.json of the plan job
  --verification=<d>  directory of the post-apply checks' markdown
  --outcome=<r>       result of the apply job (success, failure, cancelled)
  --started=<epoch>, --ended=<epoch>  when the apply job ran
  --repo=, --commit=  what the heading line names (default $GITHUB_REPOSITORY,
                      $GITHUB_SHA)
"""
import json
import os
import pathlib
import re
import sys

MAX = 60_000
MAX_ERROR_LINES = 25
NONE = "—"
RE_UNIT = re.compile(r"\[([^\]]+)\] terraform:")
RE_DONE = re.compile(r"^(\S.*?): (Creation|Modifications|Destruction|Import) complete")
RE_START = re.compile(r"^(\S.*?): (Creating|Modifying|Destroying|Importing|Reading)\.\.\.")
RE_UNIT_DONE = re.compile(r"^(?:Apply|Destroy) complete! Resources:")
RE_ERROR = re.compile(r"^\s*Error: (.+)$")
RE_WARNING = re.compile(r"^\s*Warning: ")
RE_WITH = re.compile(r"^\s*with (\S+?),\s*$")
# The comment shows a unit by its state key, the path without infrastructure/.
CONTENT = "infrastructure/"


def state_key(unit: str) -> str:
    return unit[len(CONTENT):] if unit.startswith(CONTENT) else unit


def code(text) -> str:
    text = str(text).replace("|", "\\|")
    tick = "``" if "`" in text else "`"
    pad = " " if tick == "``" else ""
    return f"{tick}{pad}{text}{pad}{tick}"


def fenced(lines: list) -> list:
    longest = max((len(r) for line in lines for r in re.findall(r"`+", line)), default=0)
    fence = "`" * max(3, longest + 1)
    return [fence, *lines, fence]


def plural(n: int, word: str) -> str:
    return f"{n} {word}{'s' * (n != 1)}"


def duration(start: str, end: str) -> str:
    try:
        s = int(end) - int(start)
    except (TypeError, ValueError):
        return ""
    if s < 0:
        return ""
    return f"{s // 60} min {s % 60} s" if s >= 60 else f"{s} s"


def read_apply(paths: list) -> dict:
    """Per unit: resources done, failed (address -> error), error blocks, finished.

    The output of the destroy of deleted units and of the apply; a file that
    is not there (nothing was destroyed) is skipped.
    """
    units = {}
    lines = []
    for path in paths:
        if path and os.path.isfile(path):
            lines += open(path, encoding="utf-8", errors="replace").readlines()
    modes = {}
    for line in lines:
        line = line.rstrip("\n")
        m = RE_UNIT.search(line)
        if not m:
            continue
        unit = state_key(m.group(1))
        label = line[:m.start()].split()
        stream = label[-1] if label and re.fullmatch(r"[A-Z]+", label[-1]) else ""
        text = line[m.end():]
        text = text[1:] if text.startswith(" ") else text
        u = units.setdefault(unit, {"done": set(), "started": set(), "failed": {},
                                    "errors": [], "finished": False})
        key = (unit, stream)
        if modes.get(key) == "error" and not (RE_ERROR.match(text) or RE_WARNING.match(text)):
            u["errors"][-1].append(text)
            if w := RE_WITH.match(text):
                u["failed"][w.group(1)] = RE_ERROR.match(u["errors"][-1][0]).group(1).strip()
            continue
        modes[key] = None
        if d := RE_DONE.match(text):
            u["done"].add(d.group(1))
        elif s := RE_START.match(text):
            u["started"].add(s.group(1))
        elif RE_UNIT_DONE.match(text):
            u["finished"] = True
        elif RE_ERROR.match(text):
            modes[key] = "error"
            u["errors"].append([text])
    return units


def past(what: str, frm: str, to: str) -> str:
    """A row of the plan comment, said of what happened."""
    if what.startswith("updated"):
        return "updated"
    if frm == "(sensitive)" or to == "(sensitive)":
        return f"{what} changed (sensitive)"
    if frm != NONE or to != NONE:
        return f"{what} changed: {frm} → {to}"
    return what


def result(entry: dict, units: dict, outcome: str) -> str:
    u = units.get(entry["unit"])
    if u is None:
        return "⏭️ not run"
    if entry["address"] in u["failed"]:
        return "❌ failed: " + u["failed"][entry["address"]].replace("|", "\\|")
    if entry["address"] in u["done"] or u["finished"]:
        return "✅ done"
    if u["errors"] or entry["address"] in u["started"] or outcome != "success":
        return "⏭️ not applied"
    return "✅ done"


def main(paths: list, opts: dict) -> int:
    changes = {}
    if opts.get("changes") and os.path.isfile(opts["changes"]):
        changes = json.loads(pathlib.Path(opts["changes"]).read_text(encoding="utf-8"))
    entries = [e for e in changes.get("entries", []) if not e.get("noop_import")]
    noop_imports = changes.get("noop_imports") or {}
    outcome = opts.get("outcome") or "success"
    units = read_apply(paths)

    results = [(e, result(e, units, outcome)) for e in entries]
    any_failed = any(r.startswith("❌") for _, r in results) or any(
        u["errors"] for u in units.values())
    ok = outcome == "success" and not any_failed

    repo = opts.get("repo") or changes.get("repo") or os.environ.get("GITHUB_REPOSITORY", "")
    commit = opts.get("commit") or os.environ.get("GITHUB_SHA", "")
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    planned = [state_key(u) for u in changes.get("units") or []] or sorted(units)
    applied = sorted({e["unit"] for e in entries} | set(units))

    line = []
    if repo:
        line.append(code(repo))
    if commit:
        line.append(f"merge [{code(commit[:7])}]({server}/{repo}/commit/{commit})"
                    if repo else code(commit[:7]))
    line.append(plural(len(applied or planned), "unit"))
    if took := duration(opts.get("started"), opts.get("ended")):
        line.append(f"⏱️ {took}")
    if run_id and repo:
        line.append(f"[run]({server}/{repo}/actions/runs/{run_id})")

    many = len({e["unit"] for e in entries}) > 1
    rows = []
    for e, res in results:
        name = code(e["name"]) + (f"<br><sub>{code(e['unit'])}</sub>" if many else "")
        for i, (what, frm, to) in enumerate(e["rows"] or [("changed", NONE, NONE)]):
            kind, label = (e["kind"], name) if i == 0 else ("", "")
            rows.append(f"| {e['icon']} | {kind} | {label} | {past(what, frm, to)} | {res} |")

    failed_blocks = {}
    for unit, u in units.items():
        for block in u["errors"]:
            failed_blocks.setdefault(tuple(block), []).append(unit)

    verification = []
    vdir = opts.get("verification")
    if vdir and os.path.isdir(vdir):
        for f in sorted(pathlib.Path(vdir).rglob("*.md")):
            text = f.read_text(encoding="utf-8").strip()
            # A check that writes its own section heading gets this one.
            text = re.sub(r"^#{1,3} Verification[ \t]*\n+", "", text).strip()
            if text:
                verification.append(text)

    def render(cap=None, error_lines=None) -> str:
        out = ["## ✅ Apply complete" if ok else "## ❌ Apply failed", "", " · ".join(line), ""]
        if rows:
            out += ["| | Resource | Name | What happened | Result |", "|---|---|---|---|---|"]
            out += rows[:cap] if cap is not None else rows
            if cap is not None and cap < len(rows):
                out.append(f"| | | | _{len(rows) - cap} more rows, see the run log_ | |")
            out.append("")
        elif not noop_imports:
            out += ["Nothing was planned to change." if outcome == "success"
                    else "No plan of this run to compare with, see the run log.", ""]
        if noop_imports:
            n = sum(noop_imports.values())
            out += [f"📥 **Imported without a change:** {n} — "
                    + ", ".join(f"{code(u)} ({c})" for u, c in sorted(noop_imports.items())) + ".", ""]
        if failed_blocks or outcome != "success":
            out += ["### ❌ Errors", ""]
            if not failed_blocks:
                out += [f"The apply job ended as `{outcome}` without an error from terraform; "
                        "see the run log.", ""]
            for block, names in failed_blocks.items():
                body = list(block)
                if error_lines is not None and len(body) > error_lines:
                    body = body[:error_lines] + [f"... {len(block) - error_lines} more lines"]
                out += [f"**{', '.join(code(u) for u in names)}**", ""] + fenced(body) + [""]
        out += ["### Verification", ""]
        out += ["\n\n".join(verification) if verification
                else "_No post-apply check has reported._", ""]
        notes = []
        if error_lines is not None:
            notes.append("long error messages are shortened")
        if cap is not None and cap < len(rows):
            notes.append(f"{len(rows) - cap} rows of the table are left out")
        if notes:
            out.append(f"_Cut to fit a comment: {'; '.join(notes)}. The full output is in the "
                       "run log._")
        return "\n".join(out).rstrip() + "\n"

    text = render()
    if len(text) > MAX:
        text = render(error_lines=MAX_ERROR_LINES)
    if len(text) > MAX:
        lo, hi = 0, len(rows)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if len(render(cap=mid, error_lines=MAX_ERROR_LINES)) <= MAX:
                lo = mid
            else:
                hi = mid - 1
        text = render(cap=lo, error_lines=MAX_ERROR_LINES)
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    args = sys.argv[1:]
    paths = [a for a in args if not a.startswith("--")]
    opts = {}
    for a in args:
        if a.startswith("--"):
            key, _, value = a[2:].partition("=")
            opts[key] = value
    sys.exit(main(paths, opts))
