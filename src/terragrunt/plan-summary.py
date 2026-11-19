#!/usr/bin/env python3
"""Turns the output of a terragrunt plan into the plan comment of a pull request.

Usage: plan-summary.py <plan.txt> [options]

The comment, top to bottom:

  ## 📋 Plan complete            (## ❌ Plan failed on an error, a unit without
                                  a result or a failed check)
  repo · commit · units · ➕ created · ✏️ updated · 🗑️ destroyed · ♻️ replaced
  ### ⚠️ Destroys and replaces   icon | Resource | Name | Unit | Why
  ### Changes                    icon | Resource | Name | What changes | From | To
  imports that change nothing, as one count line
  ### ❌ Errors, ### 🛡️ Security findings   when there are any
  **Status:** security findings, formatting and validation
  **Needed:** what has to happen next (an approval of a destroy, a fix, a merge)
  <details> per unit: what terraform printed, as a diff </details>

The tables come from the plans in JSON (--json-dir, plan_details.py): one row
per change that matters, in standard wording. A unit without a JSON plan gets
one row per resource from the text. The raw plan stays in the run artifact.

A comment on GitHub holds at most 65 536 characters. When the comment does not
fit into MAX, the details are shortened (the largest unit first), then left
out; the tables are never cut while anything else can be. What was cut is said
at the end.

The output of run --all is interleaved across units, every line prefixed with
"[<unit>] terraform:"; the lines are grouped back by unit and the prefix is
dropped.

Options:
  --fail-on-change   exit non-zero when the plan proposes anything at all or a
                     unit ended in an error (the gate of the weekly drift job;
                     terragrunt itself returns zero even when there are changes)
  --github-output    write has_changes, has_errors and has_destroys to
                     $GITHUB_OUTPUT; a plan that destroys or replaces is not
                     applied by a push, it waits for a manual run
  --json-dir=<dir>   the plans in JSON, <dir>/<unit>/tfplan.json
  --export=<file>    a share of a split plan: write the rows of its JSON plans,
                     its outputs and trivy findings, for the merge
  --import=<dir>     the merge: read every such file of the directory, next to
                     the concatenated plan.txt of all shares
  --trivy=<dir>      trivy's JSON reports over those plans, one per unit
  --checks=<list>    results of the checks of the same job, as
                     name=outcome,name=outcome (formatting=success,...)
  --changes-out=<f>  write the entries of the tables as JSON, for the apply
                     comment (apply-summary.py); text only, nothing sensitive
  --artifact=<name>  the artifact the raw plan is in (default plan-$GITHUB_RUN_ID)
  --repo=, --commit= what the heading line names (default $GITHUB_REPOSITORY,
                     $GITHUB_SHA)
  --drift            the weekly drift check: "Drift check" in the heading and
                     no "Needed" line
  --nothing-selected the change selects no unit; plan.txt may be missing
  --no-plan          no plan was produced; plan.txt may be missing

An import, a move, a forget (a removed block with destroy = false) and a change
of outputs alone count as a change too: each reaches the state only through
apply, and dependent units read outputs from there. A unit terragrunt queued
that printed neither a plan nor an error counts as an error: the run was
cancelled, or terragrunt failed before terraform started.
"""
import json
import os
import pathlib
import re
import sys

import plan_details
from plan_details import NONE, code

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

MAX = 60_000
MAX_ERROR_LINES = 25


def fenced(lines: list, lang: str = "") -> list:
    """A code block whose fence is longer than any run of backticks inside."""
    longest = max((len(r) for line in lines for r in re.findall(r"`+", line)), default=0)
    fence = "`" * max(3, longest + 1)
    return [fence + lang, *lines, fence]


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


def shared_parts(directory: str) -> tuple:
    """What the shares of a split plan exported (--export), merged.

    The JSON plans hold sensitive values in clear text and never leave the job
    that made them; a share passes on only the rows the comment shows.
    """
    entries, outputs, findings = {}, {}, set()
    if directory and os.path.isdir(directory):
        for part in sorted(pathlib.Path(directory).glob("*.json")):
            data = json.loads(part.read_text(encoding="utf-8") or "{}")
            for unit, items in (data.get("entries") or {}).items():
                entries[unit] = [{**e, "rows": [tuple(r) for r in e["rows"]]} for e in items]
            outputs.update(data.get("outputs") or {})
            findings.update(tuple(f) for f in data.get("findings") or [])
    return entries, outputs, sorted(findings)


def read_output(path: str):
    """units {unit: (add, chg, dst, imp)}, parsed per unit, loose lines, queue, errors."""
    units, errors, streams, loose, queued = {}, [], {}, [], {}
    unit, in_queue = "?", False
    if not path or not os.path.isfile(path):
        return units, {}, loose, queued, errors
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
    return units, parsed, loose, queued, errors


def plural(n: int, word: str) -> str:
    return f"{n} {word}{'s' * (n != 1)}"


def cell(text: str) -> str:
    """Text for a table cell; code spans have their pipes escaped already."""
    text = str(text).replace("\n", " ")
    return text if "`" in text else text.replace("|", "\\|")


def checks_of(spec: str) -> list:
    """[(name, outcome)] from name=outcome,name=outcome."""
    out = []
    for part in (spec or "").split(","):
        if "=" in part:
            name, outcome = part.split("=", 1)
            out.append((name.strip(), outcome.strip() or "skipped"))
    return out


def main(path: str, opts: dict) -> int:
    drift = "drift" in opts
    units, parsed, loose, queued, errors = read_output(path)

    # Every resource the plan touches, from JSON where there is one.
    shared, shared_outputs, shared_findings = shared_parts(opts.get("import"))
    entries, from_json, outputs = [], {}, {}
    for u, p in sorted(parsed.items()):
        plan_json = pathlib.Path(opts.get("json-dir") or "", u, "tfplan.json")
        got = None
        if u in shared:
            got = shared[u]
            if shared_outputs.get(u):
                outputs[u] = shared_outputs[u]
        elif opts.get("json-dir") and plan_json.is_file():
            try:
                plan = plan_details.load(plan_json)
                got = plan_details.entries(plan, u)
                outs = [k for k, v in (plan.get("output_changes") or {}).items()
                        if (v or {}).get("actions") not in (["no-op"], None)]
                if outs:
                    outputs[u] = outs
                from_json[u] = got
            except (ValueError, KeyError, TypeError, AttributeError) as e:
                print(f"::warning::{u}: the JSON plan could not be read ({e})", file=sys.stderr)
        if got is None:
            got = []
            for r in p["resources"]:
                imported = any(RE_IMPORTED.match(x) for x in r["lines"])
                if e := plan_details.text_entry(r["address"], r["what"], u, imported):
                    got.append(e)
            if p["outputs"]:
                outputs[u] = ["(see the details)"]
        entries += got
    entries.sort(key=lambda e: (plan_details.ORDER.get(e["action"], 9), e["unit"],
                                plan_details.natural(e["address"])))

    count = {a: sum(e["action"] == a for e in entries) for a in plan_details.ICONS}
    count["import"] = sum(e["action"] == "import" or e["imported"] for e in entries)
    destructive = [e for e in entries if e["action"] in ("destroy", "replace")]
    listed = [e for e in entries if e["action"] not in ("destroy", "replace") and not e["noop_import"]]
    noop_imports = {}
    for e in entries:
        if e["noop_import"]:
            noop_imports[e["unit"]] = noop_imports.get(e["unit"], 0) + 1

    # Text totals still decide: they count what the JSON might not have.
    whats = [r["what"] for p in parsed.values() for r in p["resources"]]
    text_changes = any(any(t) for t in units.values()) or any(
        w.startswith(("has moved to ", "will no longer be managed")) for w in whats)
    changes = bool(text_changes or entries or outputs)

    # The same failure (an expired login, say) tends to hit many units at once.
    failed = {}
    for u, p in parsed.items():
        for block in p["errors"]:
            failed.setdefault(tuple(block), {})[u] = None
    known = {RE_ERROR.match(b[0]).group(1).strip() for b in failed}
    for block in loose_errors(loose, known):
        failed.setdefault(tuple(block), {})[None] = None
    missing = [u for u in queued if u not in units and not parsed.get(u, {}).get("errors")]
    checks = checks_of(opts.get("checks", ""))
    bad_checks = [n for n, o in checks if o not in ("success", "skipped")]
    no_plan = "no-plan" in opts or (not parsed and not units and "nothing-selected" not in opts
                                    and not (path and os.path.isfile(path) and os.path.getsize(path)))
    is_failed = bool(failed or missing or bad_checks or no_plan)

    all_units = sorted(set(units) | set(parsed) | set(missing))
    changed_units = sorted({e["unit"] for e in entries} | set(outputs)
                           | {u for u, t in units.items() if any(t)})
    unchanged = [u for u in all_units if u not in changed_units and u in units]
    many = len({e["unit"] for e in listed}) > 1
    findings = sorted(set(shared_findings) | set(trivy_findings(opts.get("trivy"))))
    if export := opts.get("export"):
        pathlib.Path(export).write_text(json.dumps({
            "entries": from_json, "outputs": {u: outputs[u] for u in from_json if u in outputs},
            "findings": findings}, ensure_ascii=False), encoding="utf-8")

    diffs = {}
    for u in changed_units:
        p = parsed.get(u)
        if not p:
            continue
        blocks = [r["lines"] for r in p["resources"]]
        if p["outputs"]:
            blocks.append(p["outputs"])
        diffs[u] = [line for i, b in enumerate(blocks)
                    for line in ([""] if i else []) + [highlight(x) for x in b]]

    artifact = opts.get("artifact")
    if artifact is None and os.environ.get("GITHUB_RUN_ID"):
        artifact = f"plan-{os.environ['GITHUB_RUN_ID']}"
    where = f"artifact {code(artifact)} (plan.txt)" if artifact else "plan.txt"
    repo = opts.get("repo") or os.environ.get("GITHUB_REPOSITORY", "")
    commit = opts.get("commit") or os.environ.get("GITHUB_SHA", "")
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")

    title = "Drift check" if drift else "Plan"
    heading = f"## ❌ {title} failed" if is_failed else f"## 📋 {title} complete"
    line = []
    if repo:
        line.append(code(repo))
    if commit:
        line.append(f"[{code(commit[:7])}]({server}/{repo}/commit/{commit})" if repo else code(commit[:7]))
    line.append(plural(len(all_units), "unit"))
    line += [f"➕ {count['create']}", f"✏️ {count['update']}", f"🗑️ {count['destroy']}",
             f"♻️ {count['replace']}"]
    line += [f"{plan_details.ICONS[a]} {count[a]} {w}" for a, w in
             (("import", "imported"), ("move", "moved"), ("forget", "forgotten")) if count[a]]

    status = []
    if opts.get("trivy") is not None:
        status.append(f"🛡️ {plural(len(findings), 'security finding')} (below)" if findings
                      else "🛡️ no security findings")
    if checks:
        ok = [n for n, o in checks if o == "success"]
        if ok:
            status.append("✅ " + " and ".join(ok) + " passed")
        status += [f"❌ {n} failed" for n in bad_checks]
    if failed or missing:
        status.append("❌ " + plural(len({u for s in failed.values() for u in s if u} | set(missing))
                                     or 1, "unit") + " without a plan")

    needed = []
    if failed or missing or no_plan:
        needed.append("fix the errors above; the plan runs again on the next push")
    if bad_checks:
        needed.append("fix " + " and ".join(bad_checks))
    if findings:
        needed.append("fix the security findings or accept them in "
                      "`.github/trivyignore.yaml` with the reason")
    if destructive:
        needed.append("approval of the destroy: an approving review of this pull request "
                      "after this plan (`destroy / approved`), then the merge applies it")
    if not needed:
        needed.append("review and merge; the apply on `main` runs by itself" if changes
                      else "nothing; merging applies nothing")

    def render(shown: dict, details: bool = True, compact: bool = False, cap=None,
               error_lines=None) -> str:
        out = [heading, "", " · ".join(line), ""]

        if destructive:
            out += ["### ⚠️ Destroys and replaces", "",
                    "| | Resource | Name | Unit | Why |", "|---|---|---|---|---|"]
            out += [f"| {e['icon']} | {e['kind']} | {code(e['name'])} | {code(e['unit'])} "
                    f"| {cell(e['why']) or NONE} |" for e in destructive]
            out.append("")

        rows = []
        for e in listed:
            name = code(e["name"]) + (f"<br><sub>{code(e['unit'])}</sub>" if many else "")
            those = e["rows"]
            if compact and len(those) > 1:
                those = [(f"{plural(len(those), 'change')}, see the details", NONE, NONE)]
            # The rows after the first of a resource leave its kind and name
            # out, so that one resource reads as one block.
            for i, (what, frm, to) in enumerate(those):
                kind, label = (e["kind"], name) if i == 0 else ("", "")
                rows.append(f"| {e['icon']} | {kind} | {label} | {cell(what)} | {frm} | {to} |")
        if rows or not destructive:
            out += ["### Changes", ""]
        if rows:
            out += ["| | Resource | Name | What changes | From | To |",
                    "|---|---|---|---|---|---|"]
            out += rows[:cap] if cap is not None else rows
            if cap is not None and cap < len(rows):
                out.append(f"| | | | _{len(rows) - cap} more rows, see {where}_ | | |")
            out.append("")
        elif not destructive and not noop_imports:
            if outputs:
                out += ["Only output values change, in " + ", ".join(code(u) for u in outputs)
                        + ".", ""]
            elif "nothing-selected" in opts:
                out += ["Nothing to plan: the change selects no unit.", ""]
            elif not is_failed:
                out += ["None: the plan matches what is deployed.", ""]
            else:
                out += ["No plan to show.", ""]
        if noop_imports:
            n = sum(noop_imports.values())
            out += [f"📥 **Imported without a change:** {n} — "
                    + ", ".join(f"{code(u)} ({c})" for u, c in sorted(noop_imports.items())) + ".", ""]

        if is_failed and (failed or missing or no_plan):
            out += ["### ❌ Errors", ""]
            if no_plan and not failed and not missing:
                out += [f"No plan was produced, see the run log and {where}.", ""]
            if missing:
                out += ["**Without a result:** " + ", ".join(code(u) for u in missing), "",
                        "Queued, but neither a plan nor an error came out: the run was "
                        "cancelled, or terragrunt failed before terraform started.", ""]
            for block, sources in failed.items():
                names = [code(u) for u in sources if u is not None]
                if None in sources:
                    names.append("outside a unit")
                body = list(block)
                if error_lines is not None and len(body) > error_lines:
                    body = body[:error_lines] + [f"... {len(block) - error_lines} more lines"]
                out += [f"**{', '.join(names)}**", ""] + fenced(body) + [""]

        if findings:
            out += ["### 🛡️ Security findings", "",
                    "trivy over the plan, HIGH and CRITICAL. A finding that is accepted "
                    "goes into `.github/trivyignore.yaml` with the reason.", "",
                    "| Unit | Severity | Check | Resource |", "|---|---|---|---|"]
            out += [f"| {code(u)} | {sev} | {cell(cid)}: {cell(title_)} | {code(res) if res else ''} |"
                    for u, sev, cid, title_, res in findings]
            out.append("")

        if status:
            out += ["**Status:** " + " · ".join(status), ""]
        if not drift and len(needed) == 1:
            out += [f"**Needed:** {needed[0]}.", ""]
        elif not drift:
            out += ["**Needed:**", ""] + [f"- {n}" for n in needed] + [""]

        cut_notes = []
        body = []
        for u in changed_units:
            lines = diffs.get(u) or []
            if not lines:
                continue
            p = parsed[u]
            n = shown.get(u, len(lines))
            body.append(f"#### {code(u)}" + (f" ({p['plan']})" if p["plan"] else ""))
            body.append("")
            if n == 0:
                body += ["_Left out, the comment would be too long._", ""]
                cut_notes.append(code(u))
                continue
            text = lines[:n]
            while text and not text[-1]:
                text.pop()
            body += fenced(text, "diff")
            if n < len(lines):
                body += ["", f"_{len(lines) - n} of {len(lines)} lines left out._"]
                cut_notes.append(code(u))
            body.append("")
        if unchanged:
            body.append(f"**{plural(len(unchanged), 'unit')} without a change:** "
                        + ", ".join(code(u) for u in unchanged))
            body.append("")
        if body and details:
            out += ["<details><summary>Details per unit</summary>", ""] + body + ["</details>", ""]

        notes = []
        if not details and body:
            notes.append("the details per unit are left out")
        elif cut_notes:
            notes.append("the details of " + ", ".join(cut_notes) + " are shortened")
        if compact:
            notes.append("resources with several changes are one row each")
        if error_lines is not None and any(len(b) > error_lines for b in failed):
            notes.append("long error messages are shortened")
        if notes:
            out.append(f"_Cut to fit a comment: {'; '.join(notes)}. The full plan is in {where}._")
        else:
            out.append(f"_The raw plan is in {where}._")
        return "\n".join(out)

    # The details go first, from the largest unit down: as much of the
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
    if diffs and all(shown.get(u, len(d)) == 0 for u, d in diffs.items()):
        text = render(shown, details=False)
    attempts = [dict(details=False), dict(details=False, error_lines=MAX_ERROR_LINES),
                dict(details=False, error_lines=MAX_ERROR_LINES, compact=True)]
    for kwargs in attempts:
        if len(text) <= MAX:
            break
        text = render(shown, **kwargs)
    if len(text) > MAX:
        # Last of all the rows of the changes table; the destroys stay whole.
        lo, hi = 0, len(listed) * 50
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if len(render(shown, details=False, error_lines=MAX_ERROR_LINES, compact=True,
                          cap=mid)) <= MAX:
                lo = mid
            else:
                hi = mid - 1
        text = render(shown, details=False, error_lines=MAX_ERROR_LINES, compact=True, cap=lo)
    if len(text) > MAX:
        text = cut(text, MAX, where)
    print(text)

    if out_path := opts.get("changes-out"):
        pathlib.Path(out_path).write_text(json.dumps({
            "repo": repo, "commit": commit, "units": all_units, "entries": entries,
            "noop_imports": noop_imports, "failed": is_failed,
        }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    if "github-output" in opts and (path_out := os.environ.get("GITHUB_OUTPUT")):
        with open(path_out, "a", encoding="utf-8") as fh:
            fh.write(f"has_changes={str(changes).lower()}\n")
            fh.write(f"has_errors={str(bool(errors or missing)).lower()}\n")
            has_destroys = bool(destructive) or any(
                r["what"] == "will be destroyed" or "replaced" in r["what"]
                for p in parsed.values() for r in p["resources"])
            fh.write(f"has_destroys={str(has_destroys).lower()}\n")

    if "fail-on-change" in opts and (changes or errors or missing):
        totals = ", ".join(f"{count[a]} {w}" for a, w in (
            ("create", "to create"), ("update", "to update"), ("destroy", "to destroy"),
            ("replace", "to replace"), ("import", "to import"), ("move", "to move"),
            ("forget", "to forget")) if count[a] or a in ("create", "update", "destroy"))
        if outputs:
            totals += f", outputs change in {plural(len(outputs), 'unit')}"
        print(f"\nDRIFT: {totals}, {plural(len(errors), 'error')}"
              + (f", {plural(len(missing), 'unit')} without a result" if missing else "") + ".",
              file=sys.stderr)
        return 1
    return 0


def cut(text: str, limit: int, where: str) -> str:
    """Last resort when even the shortest form is too long.

    Cuts whole lines from the end and closes a code block left open.
    """
    note = f"\n\n_Cut off to fit a comment; the full plan is in {where}._"
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
    opts = {}
    for a in args:
        if a.startswith("--"):
            key, _, value = a[2:].partition("=")
            opts[key] = value
    sys.exit(main(paths[0] if paths else "", opts))
