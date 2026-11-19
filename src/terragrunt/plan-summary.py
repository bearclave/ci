#!/usr/bin/env python3
"""Turns raw terragrunt output into a markdown plan summary.

The output of run --all is interleaved across units, every line prefixed with
"[<unit>] terraform:". The lines are grouped back by unit and the prefix is
dropped, so a pull request shows per unit what terraform itself printed: each
resource with its action and, folded away, its attribute diff.

A comment on GitHub holds at most 65 536 characters. When the plan does not fit
into MAX, the attribute diffs are shortened, the largest unit first. The
totals, the errors and the list of resources always stay whole; the full
output is in the plan artifact.

With --fail-on-change it returns a non-zero code when the plan proposes
anything at all or when any unit ended in an error. That makes it usable as the
gate of the weekly drift job, since terragrunt itself returns zero even when
there are changes.

With --github-output the same verdict is written to $GITHUB_OUTPUT as
has_changes and has_errors, which is what decides whether apply runs at all,
and has_destroys, true when anything is destroyed or replaced: such an apply
does not run by itself after a merge, it waits for a manual run.

An import, a move, a forget (a removed block with destroy = false) and a change
of outputs alone count as a change too. None of them touches Azure and
terraform leaves them out of its "Plan:" line or prints none at all, yet each
reaches the state only through apply; dependent units read outputs from there.

A unit terragrunt queued that printed neither a plan nor an error counts as an
error: the run was cancelled, or terragrunt failed before terraform started.
Without that, a cut-off run would read as a clean one.

--artifact=<name> names the artifact the full output is uploaded to; it
defaults to plan-$GITHUB_RUN_ID.

--json-dir=<dir> points to the plans in JSON, one <dir>/<unit>/tfplan.json per
unit (terragrunt's --json-out-dir). A unit that has one is listed as a table
from plan_details.py - what each resource is, its name, what changes from what
to what - instead of the bare resource addresses.

--trivy=<dir> adds the findings of trivy over those plans (a directory of its
JSON reports, one per unit) as a section of their own.
"""
import json
import os
import pathlib
import re
import sys

import plan_details

RE_UNIT = re.compile(r"\[([^\]]+)\] terraform:")
RE_PLAN = re.compile(
    r"Plan: (?:(\d+) to import, )?(\d+) to add, (\d+) to change, (\d+) to destroy")
# The second form is what a destroy plan prints when the state is empty.
RE_NOOP = re.compile(
    r"No changes\. (?:Your infrastructure matches the configuration"
    r"|No objects need to be destroyed)")
RE_ERROR = re.compile(r"^\s*Error: (.+)$")
RE_WARNING = re.compile(r"^\s*Warning: ")
# The queue terragrunt prints before it starts, one unit per branch of a tree.
RE_QUEUE = re.compile(r"The following units will be run")
RE_BRANCH = re.compile(r"^[\s│]*[├╰└]── (\S+)\s*$")
# The heading terraform puts above each resource in the plan. Headings sit at
# the left edge; the comments inside a resource are indented deeper.
RE_RESOURCE = re.compile(
    r"^ {0,2}# (.+?) (will be created|will be updated in-place|will be destroyed"
    r"|(?:is tainted, so )?must be replaced|will be replaced, as requested"
    r"|will be read during apply|will be imported"
    r"|will no longer be managed by Terraform.*|has moved to .+)$"
)
RE_IMPORTED = re.compile(r"^ {0,2}# \(imported from ")
# Terraform indents in steps of four and puts the action symbol two columns
# in, so a symbol anywhere else is part of a value.
RE_SIGN = re.compile(r"^( +)([-+~]) ")

ACTIONS = {
    "will be created": "create",
    "will be updated in-place": "update in-place",
    "will be destroyed": "destroy",
    "must be replaced": "replace",
    "is tainted, so must be replaced": "replace (tainted)",
    "will be replaced, as requested": "replace (as requested)",
    "will be read during apply": "read",
    "will be imported": "import",
}

MAX = 60_000


def code(text: str) -> str:
    """An inline code span that survives backticks in the text."""
    tick = "``" if "`" in text else "`"
    pad = " " if tick == "``" else ""
    return f"{tick}{pad}{text}{pad}{tick}"


def fenced(lines: list, lang: str = "") -> list:
    """A code block whose fence is longer than any run of backticks inside."""
    longest = max((len(r) for line in lines for r in re.findall(r"`+", line)), default=0)
    fence = "`" * max(3, longest + 1)
    return [fence + lang, *lines, fence]


def action(what: str) -> str:
    if what.startswith("has moved to "):
        return "move to " + code(what[len("has moved to "):])
    if what.startswith("will no longer be managed"):
        return "forget"
    return ACTIONS[what]


def highlight(line: str) -> str:
    """Moves the action symbol to the first column, where a diff block colours it.

    `~` becomes `!`, the marker of a changed line in a diff block. The rest of
    the line keeps its columns.
    """
    m = RE_SIGN.match(line)
    if not m or len(m.group(1)) % 4 != 2:
        return line
    sign = "!" if m.group(2) == "~" else m.group(2)
    return sign + m.group(1) + line[m.end() - 1:]


def parse(streams: dict) -> dict:
    """Splits the output of one unit into resources, outputs, errors and the plan line.

    Each stream (STDOUT, STDERR, ...) is read on its own: terraform prints the
    plan to stdout and the errors to stderr, and the two can interleave.
    """
    unit = {"resources": [], "outputs": [], "errors": [], "plan": None}
    for lines in streams.values():
        mode = None
        for text in lines:
            in_block = text[:1].isspace() or text.startswith(("-/+", "+/-"))
            if mode == "resources" and in_block:
                if m := RE_RESOURCE.match(text):
                    unit["resources"].append({"address": m.group(1), "what": m.group(2),
                                              "lines": [text]})
                elif unit["resources"]:
                    unit["resources"][-1]["lines"].append(text)
                continue
            if mode == "outputs" and text[:1].isspace():
                unit["outputs"].append(text)
                continue
            if mode == "error" and not (RE_ERROR.match(text) or RE_WARNING.match(text)):
                unit["errors"][-1].append(text)
                continue
            mode = None
            if text.startswith("Terraform will perform the following actions:"):
                mode = "resources"
            elif text.startswith("Changes to Outputs:"):
                mode = "outputs"
                unit["outputs"].append(text)
            elif text.startswith("Plan: "):
                unit["plan"] = text[len("Plan: "):].rstrip(".")
            elif RE_ERROR.match(text):
                mode = "error"
                unit["errors"].append([text])
    return unit


def loose_errors(lines: list, known: set) -> list:
    """Error blocks terragrunt printed outside any unit and no unit reported.

    Terragrunt repeats every failed unit's error in its closing report, so an
    error seen in a unit already is skipped. A block runs to the next empty line.
    """
    blocks = {}
    for i, line in enumerate(lines):
        m = RE_ERROR.match(line.split("terraform:")[-1])
        if not m or m.group(1).strip() in known or m.group(1).strip() in blocks:
            continue
        indent = len(line) - len(line.lstrip())
        block = []
        for follow in lines[i:]:
            if not follow:
                break
            block.append(follow[indent:] if follow[:indent].isspace() else follow.strip())
        blocks[m.group(1).strip()] = [b.rstrip() for b in block]
    return list(blocks.values())


def trivy_findings(reports: str) -> list:
    """(unit, severity, id, title, resource) for every failed trivy check.

    Reads every JSON report in the directory, one per unit; the target of a
    result is the plan file as <unit>/tfplan.json.
    """
    if not reports or not os.path.isdir(reports):
        return []
    found = set()
    for report_path in sorted(pathlib.Path(reports).glob("*.json")):
        report = json.loads(report_path.read_text(encoding="utf-8") or "{}")
        for result in report.get("Results") or []:
            unit = report.get("Unit") or os.path.dirname(result.get("Target", "")) or "?"
            for m in result.get("Misconfigurations") or []:
                if m.get("Status") != "FAIL":
                    continue
                found.add((unit, m.get("Severity", ""), m.get("AVDID") or m.get("ID", ""),
                           m.get("Title", ""), (m.get("CauseMetadata") or {}).get("Resource", "")))
    return sorted(found)


def main(path: str, fail_on_change: bool = False, github_output: bool = False,
         artifact: str = None, json_dir: str = None, trivy: str = None) -> int:
    units, errors = {}, []
    streams, loose, queued = {}, [], {}
    unit, in_queue = "?", False
    for line in open(path, encoding="utf-8", errors="replace"):
        line = line.rstrip("\n")
        if in_queue and (m := RE_BRANCH.match(line)):
            queued[m.group(1)] = None
        elif line.strip() != ".":
            in_queue = bool(RE_QUEUE.search(line))
        m = RE_UNIT.search(line)
        if m:
            unit = m.group(1)
            label = line[:m.start()].split()
            stream = label[-1] if label and re.fullmatch(r"[A-Z]+", label[-1]) else ""
            text = line[m.end():]
            streams.setdefault(unit, {}).setdefault(stream, []).append(
                text[1:] if text.startswith(" ") else text)
        else:
            loose.append(line)
        if m := RE_PLAN.search(line):
            imp, add, chg, dst = (int(g or 0) for g in m.groups())
            units[unit] = (add, chg, dst, imp)
        elif RE_NOOP.search(line):
            units.setdefault(unit, (0, 0, 0, 0))
        if m := RE_ERROR.match(line.split("terraform:")[-1]):
            errors.append(m.group(1).strip())

    parsed = {u: parse(s) for u, s in streams.items()}
    # Terraform prints no "Plan:" line when only outputs change.
    for u, p in parsed.items():
        if p["outputs"]:
            units.setdefault(u, (0, 0, 0, 0))

    add = sum(u[0] for u in units.values())
    chg = sum(u[1] for u in units.values())
    dst = sum(u[2] for u in units.values())
    imp = sum(u[3] for u in units.values())
    whats = [r["what"] for p in parsed.values() for r in p["resources"]]
    mov = sum(w.startswith("has moved to ") for w in whats)
    fgt = sum(w.startswith("will no longer be managed") for w in whats)
    outs = sum(bool(p["outputs"]) for p in parsed.values())
    changes = any((add, chg, dst, imp, mov, fgt, outs))
    totals = ((f"{imp} to import, " if imp else "")
              + f"{add} to add, {chg} to change, {dst} to destroy"
              + (f", {mov} to move" if mov else "")
              + (f", {fgt} to forget" if fgt else "")
              + (f", outputs change in {outs} unit{'s' * (outs != 1)}" if outs else ""))
    changed = sorted(
        (u for u, p in parsed.items()
         if p["resources"] or p["outputs"] or any(units.get(u, (0, 0, 0, 0)))),
        key=lambda u: (-sum(units.get(u, (0, 0, 0, 0))), u),
    )
    unchanged = sorted(u for u in units if u not in changed and u in parsed)

    # The same failure (an expired login, say) tends to hit many units at once.
    failed = {}
    for u, p in parsed.items():
        for block in p["errors"]:
            failed.setdefault(tuple(block), {})[u] = None
    known = {RE_ERROR.match(b[0]).group(1).strip() for b in failed}
    for block in loose_errors(loose, known):
        failed.setdefault(tuple(block), {})[None] = None
    missing = [u for u in queued
               if u not in units and not parsed.get(u, {}).get("errors")]

    diffs = {}
    for u in changed:
        blocks = [r["lines"] for r in parsed[u]["resources"]]
        if parsed[u]["outputs"]:
            blocks.append(parsed[u]["outputs"])
        diffs[u] = [line for i, b in enumerate(blocks)
                    for line in ([""] if i else []) + [highlight(x) for x in b]]

    destructive = [f"{u}: {r['address']} {r['what']}"
                   for u in changed for r in parsed[u]["resources"]
                   if r["what"] == "will be destroyed" or "replaced" in r["what"]]

    tables = {}
    for u in changed:
        plan_json = pathlib.Path(json_dir or "", u, "tfplan.json")
        if json_dir and plan_json.is_file():
            try:
                tables[u] = plan_details.table(plan_details.load(plan_json))
            except (ValueError, KeyError, TypeError, AttributeError) as e:
                print(f"::warning::{u}: the JSON plan could not be read ({e})", file=sys.stderr)
    findings = trivy_findings(trivy)

    if artifact is None and os.environ.get("GITHUB_RUN_ID"):
        artifact = f"plan-{os.environ['GITHUB_RUN_ID']}"
    where = f"artifact {code(artifact)} (plan.txt)" if artifact else "plan.txt"

    def render(shown: dict) -> str:
        out = ["## Terragrunt plan\n",
               f"**{totals}** across {len(units)} unit{'s' * (len(units) != 1)}.\n"]

        if failed or missing:
            out.append("### Errors\n")
        if missing:
            out.append("#### Without a result\n")
            out.append(", ".join(code(u) for u in missing) + "\n")
            out.append("Queued, but neither a plan nor an error came out: the run was "
                       "cancelled, or terragrunt failed before terraform started. "
                       f"The rest is in {where}.\n")
        if failed:
            for block, sources in failed.items():
                names = [code(u) for u in sources if u is not None]
                if None in sources:
                    names.append("outside a unit")
                out.append(f"#### {', '.join(names)}\n")
                out += fenced(list(block))
                out.append("")

        if findings:
            out.append("### Security findings\n")
            out.append("trivy over the plan, HIGH and CRITICAL. A finding that is accepted "
                       "goes into `.github/trivyignore.yaml` with the reason.\n")
            out += ["| Unit | Severity | Check | Resource |", "|---|---|---|---|"]
            out += [f"| {code(u)} | {sev} | {cid}: {title} | {code(res) if res else ''} |"
                    for u, sev, cid, title, res in findings]
            out.append("")

        if destructive:
            out.append("### Destroys or replaces\n")
            out.append("After a merge the apply does not run these by itself: it waits "
                       "for a manual run of the workflow with `allow_destroy`.\n")
            out += [f"- {code(d)}" for d in destructive]
            out.append("")

        if changed:
            out.append("### Units with a change\n")
        for u in changed:
            p = parsed[u]
            out.append(f"#### {code(u)}" + (f" ({p['plan']})" if p["plan"] else "") + "\n")
            if u in tables:
                out += tables[u]
            else:
                for r in p["resources"]:
                    label = action(r["what"])
                    if any(RE_IMPORTED.match(x) for x in r["lines"]):
                        label += " (imported)"
                    out.append(f"- {code(r['address'])}: {label}")
                if p["outputs"]:
                    out.append("- output values change")
            out.append("")

            lines = diffs[u]
            n = shown.get(u, len(lines))
            if not lines:
                continue
            if n == 0:
                out.append("_Attribute diff left out, the comment would be too long._\n")
                continue
            title = "Attribute diff" + (", shortened" if n < len(lines) else "")
            out += [f"<details><summary>{title}</summary>", ""]
            body = lines[:n]
            while body and not body[-1]:
                body.pop()
            out += fenced(body, "diff")
            if n < len(lines):
                out += ["", f"_{len(lines) - n} of {len(lines)} lines left out._"]
            out += ["", "</details>", ""]

        if unchanged:
            noun = "unit" if len(unchanged) == 1 else "units"
            out.append(f"**{len(unchanged)} {noun} without a change:** "
                       + ", ".join(code(u) for u in unchanged))

        if shown:
            out += ["", f"_Shortened to fit a comment; the full output is in {where}._"]
        return "\n".join(out)

    # Diffs are shortened from the largest unit down: first as much of the
    # largest as the overflow needs, then the next one if that was not enough.
    shown = {}
    text = render(shown)
    for u in sorted(diffs, key=lambda u: -sum(len(x) + 1 for x in diffs[u])):
        n = len(diffs[u])
        while n and len(text) > MAX:
            over = len(text) - MAX
            while n and over > 0:
                n -= 1
                over -= len(diffs[u][n]) + 1
            shown[u] = n
            text = render(shown)
    if len(text) > MAX:
        text = cut(text, MAX, where)
    print(text)

    if github_output and (path_out := os.environ.get("GITHUB_OUTPUT")):
        with open(path_out, "a", encoding="utf-8") as fh:
            fh.write(f"has_changes={str(changes).lower()}\n")
            fh.write(f"has_errors={str(bool(errors or missing)).lower()}\n")
            fh.write(f"has_destroys={str(bool(destructive)).lower()}\n")

    if fail_on_change and (changes or errors or missing):
        print(f"\nDRIFT: {totals}, {len(errors)} errors"
              + (f", {len(missing)} unit{'s' * (len(missing) != 1)} without a result"
                 if missing else "") + ".",
              file=sys.stderr)
        return 1
    return 0


def cut(text: str, limit: int, where: str) -> str:
    """Last resort when even without the diffs the summary is too long.

    Cuts whole lines from the end and closes a code block left open.
    """
    note = f"\n\n_Cut off to fit a comment; the full output is in {where}._"
    kept, size, fence = [], 0, None
    for line in text.split("\n"):
        after = fence
        if m := re.match(r"^(`{3,})", line):
            if fence is None:
                after = m.group(1)
            elif line.strip() == fence:
                after = None
        if size + len(line) + 1 + (len(after) + 1 if after else 0) + len(note) > limit:
            break
        kept.append(line)
        size += len(line) + 1
        fence = after
    if fence:
        kept.append(fence)
    return "\n".join(kept) + note


if __name__ == "__main__":
    args = sys.argv[1:]
    paths = [a for a in args if not a.startswith("--")]
    named = dict(a[2:].split("=", 1) for a in args if a.startswith("--") and "=" in a)
    sys.exit(main(paths[0], "--fail-on-change" in args, "--github-output" in args,
                  named.get("artifact"), named.get("json-dir"), named.get("trivy")))
