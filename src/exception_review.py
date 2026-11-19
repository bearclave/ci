#!/usr/bin/env python3
"""Checks the exceptions of a repository: each says why, and when to look again.

Usage: exception_review.py [--today YYYY-MM-DD] <kind>=<file> [<kind>=<file> ...]

Kinds and the file each reads (the YAML ones converted to JSON by yq first,
so that this needs nothing beyond the standard library):

  conventions  .github/conventions.yaml   exceptions[]: rule, path, reason, review
                                          layout.exceptions[]: path, reason, review (optional)
  sentinel     .github/sentinel-lint.yaml exceptions[]: rule, path, reason, review
  trivy        .github/trivyignore.yaml   <section>[]: id, paths, statement, review
  gitleaks     .gitleaks.toml             [[allowlists]]: description ending in
                                          "review: YYYY-MM-DD"

`review` is the date (YYYY-MM-DD) by which the exception is looked at again:
still needed, still for the same reason. A date that has passed is a warning,
not a failure - the finding stays accepted until someone decides. A missing
reason or a date that is not a date fails; a missing date is a warning.

`<file>` is what the annotation names; `<kind>=<file>:<json>` reads the
converted JSON from <json> and names <file>.
"""
import datetime
import json
import os
import re
import sys
import tomllib

RE_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
RE_GITLEAKS_REVIEW = re.compile(r"^\s*review:\s*(\S+)\s*$", re.I | re.M)
TRIVY_SECTIONS = ("misconfigurations", "vulnerabilities", "secrets", "licenses")


def entries(kind: str, data) -> list:
    """(label, reason, review, required) for every exception in the file."""
    out = []
    if kind == "conventions":
        for e in (data or {}).get("exceptions") or []:
            out.append((f"{e.get('rule')} {e.get('path', '')}".strip(), e.get("reason"), e.get("review"), True))
        for e in ((data or {}).get("layout") or {}).get("exceptions") or []:
            # The layout exceptions are structure (a folder that has to be
            # where it is); a review date is welcome but not asked for.
            out.append((f"layout {e.get('path')}", e.get("reason"), e.get("review"), False))
    elif kind == "sentinel":
        for e in (data or {}).get("exceptions") or []:
            rule = e.get("rule") if "check" not in e else e.get("check")
            path = e.get("path") if "check" not in e else e.get("rule")
            out.append((f"{rule} {path or ''}".strip(), e.get("reason"), e.get("review"), True))
    elif kind == "trivy":
        for section in TRIVY_SECTIONS:
            for e in (data or {}).get(section) or []:
                paths = e.get("paths") or []
                where = f" ({len(paths)} paths)" if len(paths) > 1 else (f" {paths[0]}" if paths else "")
                out.append((f"{e.get('id')}{where}", e.get("statement"), e.get("review"), True))
    elif kind == "gitleaks":
        lists = list(data.get("allowlists") or [])
        if data.get("allowlist"):
            lists.insert(0, data["allowlist"])
        for k, e in enumerate(lists):
            description = str(e.get("description") or "")
            m = RE_GITLEAKS_REVIEW.search(description)
            reason = RE_GITLEAKS_REVIEW.sub("", description).strip()
            out.append((f"allowlist {k + 1}", reason, m.group(1) if m else None, True))
    else:
        raise ValueError(f"unknown kind {kind!r}")
    return out


def review(kind: str, data, today: datetime.date) -> tuple:
    """(errors, warnings, rows) for one file; rows feed the job summary."""
    errors, warnings, rows = [], [], []
    for label, reason, date, required in entries(kind, data):
        if not str(reason or "").strip():
            errors.append(f"exception {label} has no reason")
        if date in (None, ""):
            if required:
                warnings.append(f"exception {label} has no review date (review: YYYY-MM-DD)")
            continue
        date = str(date)
        if not RE_DATE.match(date):
            errors.append(f"exception {label}: review {date!r} is not a date (YYYY-MM-DD)")
            continue
        try:
            due = datetime.date.fromisoformat(date)
        except ValueError:
            errors.append(f"exception {label}: review {date!r} is not a date (YYYY-MM-DD)")
            continue
        if due < today:
            warnings.append(f"exception {label} was due for review on {date}: is it still needed?")
        rows.append((label, date, due < today))
    return errors, warnings, rows


def load(kind: str, path: str):
    if kind == "gitleaks":
        with open(path, "rb") as fh:
            return tomllib.load(fh)
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def escape(text: str) -> str:
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def main(args: list) -> int:
    today = datetime.date.today()
    if args[:1] == ["--today"]:
        today, args = datetime.date.fromisoformat(args[1]), args[2:]
    if not args:
        print(__doc__)
        return 2
    failed, summary = False, []
    for arg in args:
        kind, _, rest = arg.partition("=")
        name, _, source = rest.partition(":")
        try:
            data = load(kind, source or name)
            errors, warnings, rows = review(kind, data, today)
        except (OSError, ValueError, tomllib.TOMLDecodeError) as e:
            errors, warnings, rows = [f"cannot read: {e}"], [], []
        for e in errors:
            print(f"::error file={name},title=exception::{escape(e)}")
        for w in warnings:
            print(f"::warning file={name},title=exception review::{escape(w)}")
        failed |= bool(errors)
        summary += [f"| `{name}` | {label.replace('|', '/')} | {date}{' - **due**' if due else ''} |"
                    for label, date, due in rows]
    if summary:
        text = "## Exceptions\n\n| File | Exception | Review |\n|---|---|---|\n" + "\n".join(summary) + "\n"
        if os.environ.get("GITHUB_STEP_SUMMARY"):
            with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as fh:
                fh.write(text)
        else:
            print(text)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
