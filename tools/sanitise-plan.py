#!/usr/bin/env python3
"""Turns terraform plans in JSON into test fixtures that hold nothing secret.

Usage: tools/sanitise-plan.py <plan-json-dir> <out-dir>

<plan-json-dir> holds <unit>/tfplan.json (terragrunt's --json-out-dir). Each
plan is written to <out-dir>/<unit>/tfplan.json with only what the comments
read: resource_changes and the actions of output_changes. On the way:

- every value terraform marks sensitive becomes "(redacted)";
- every GUID becomes 00000000-0000-0000-0000-<n>, the same GUID the same n;
- every e-mail address becomes user<n>@example.com;
- a resource without a change keeps its address and actions but no values;
- prior_state, configuration, variables and planned_values are left out.
"""
import json
import pathlib
import re
import sys

RE_GUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
RE_MAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
KEEP = ("address", "previous_address", "module_address", "mode", "type", "name", "index",
        "provider_name", "action_reason")


class Masks:
    def __init__(self):
        self.guids, self.mails = {}, {}

    def text(self, value: str) -> str:
        value = RE_GUID.sub(lambda m: self.guid(m.group(0)), value)
        return RE_MAIL.sub(lambda m: self.mail(m.group(0)), value)

    def guid(self, g: str) -> str:
        n = self.guids.setdefault(g.lower(), len(self.guids) + 1)
        return f"00000000-0000-0000-0000-{n:012d}"

    def mail(self, m: str) -> str:
        n = self.mails.setdefault(m.lower(), len(self.mails) + 1)
        return f"user{n}@example.com"

    def value(self, value, sensitive=None):
        if sensitive is True:
            return "(redacted)"
        if isinstance(value, dict):
            s = sensitive if isinstance(sensitive, dict) else {}
            return {self.text(k): self.value(v, s.get(k)) for k, v in value.items()}
        if isinstance(value, list):
            s = sensitive if isinstance(sensitive, list) else []
            return [self.value(v, s[i] if i < len(s) else None) for i, v in enumerate(value)]
        if isinstance(value, str):
            return self.text(value)
        return value


def sanitise(plan: dict, masks: Masks) -> dict:
    out = {"format_version": plan.get("format_version"),
           "terraform_version": plan.get("terraform_version"),
           "resource_changes": [], "output_changes": {}}
    for rc in plan.get("resource_changes") or []:
        change = rc.get("change") or {}
        actions = change.get("actions") or []
        item = {k: masks.value(rc[k]) for k in KEEP if k in rc}
        new = {"actions": actions}
        noop = actions == ["no-op"] and not change.get("importing") \
            and rc.get("previous_address") in (None, rc.get("address"))
        if not noop:
            new["before"] = masks.value(change.get("before"), change.get("before_sensitive"))
            new["after"] = masks.value(change.get("after"), change.get("after_sensitive"))
            for key in ("after_unknown", "before_sensitive", "after_sensitive", "replace_paths"):
                if key in change:
                    new[key] = masks.value(change[key])
        if change.get("importing"):
            new["importing"] = {"id": masks.text(str(change["importing"].get("id", "")))}
        item["change"] = new
        out["resource_changes"].append(item)
    for name, oc in (plan.get("output_changes") or {}).items():
        out["output_changes"][name] = {"actions": oc.get("actions")}
    return out


def main(src: str, dst: str) -> int:
    masks = Masks()
    for path in sorted(pathlib.Path(src).rglob("tfplan.json")):
        unit = path.parent.relative_to(src)
        target = pathlib.Path(dst, unit, "tfplan.json")
        target.parent.mkdir(parents=True, exist_ok=True)
        plan = json.loads(path.read_text(encoding="utf-8"))
        target.write_text(json.dumps(sanitise(plan, masks), indent=1, ensure_ascii=False) + "\n",
                          encoding="utf-8")
        print(target)
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    sys.exit(main(sys.argv[1], sys.argv[2]))
