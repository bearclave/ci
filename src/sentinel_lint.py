#!/usr/bin/env python3
"""Checks Microsoft Sentinel analytic rules before they are deployed.

Usage: sentinel_lint.py <config.json> [<repository root>]

The workflow converts sentinel-lint.yaml to JSON first (yq). The config:

  rules:       [{path, format, substitutions_from}]
               format "pair": <name>.json with kind and properties, the query
               in <name>.kql beside it, ${name} placeholders filled from the
               `substitutions` of the terragrunt.hcl in substitutions_from.
               format "arm": ARM templates whose resources include
               .../alertRules, the query inside.
  watchlists:  files that define watchlists; an alias is `alias = "X"` (HCL)
               or "watchlistAlias": "X" (ARM)
  checks:      {<check>: error | warn | off}, error when not listed
  exceptions:  [{check, rule (a glob of the file), reason}]

What a deploy would only reject after the merge, or would accept and then
quietly do the wrong thing with, is caught here: a broken {{column}} in the
alert name, a tactic Sentinel does not know, more entities than Sentinel takes,
a frequency longer than the period it looks at (events in between are never
seen), grouping by an entity the rule does not map, a watchlist that does not
exist. The limits and the enumerations follow the alertRules REST API.
"""
import json
import os
import re
import subprocess
import sys

TACTICS = {
    "Reconnaissance", "ResourceDevelopment", "InitialAccess", "Execution", "Persistence",
    "PrivilegeEscalation", "DefenseEvasion", "CredentialAccess", "Discovery", "LateralMovement",
    "Collection", "Exfiltration", "CommandAndControl", "Impact", "PreAttack",
    "ImpairProcessControl", "InhibitResponseFunction",
}
ENTITY_TYPES = {
    "Account", "Host", "IP", "Malware", "File", "Process", "CloudApplication", "DNS",
    "AzureResource", "FileHash", "RegistryKey", "RegistryValue", "SecurityGroup", "URL",
    "Mailbox", "MailCluster", "MailMessage", "SubmissionMail",
}
KINDS = {"Scheduled", "NRT", "Fusion", "MicrosoftSecurityIncidentCreation",
         "MLBehaviorAnalytics", "ThreatIntelligence"}
SEVERITIES = {"High", "Medium", "Low", "Informational"}
OPERATORS = {"GreaterThan", "LessThan", "Equal", "NotEqual"}
ALERT_DETAILS = {"DisplayName", "Severity"}
MAX_ENTITIES, MAX_FIELDS, MAX_CUSTOM, MAX_PARAMS = 10, 3, 20, 3
MIN_SCHEDULE, MAX_SCHEDULE = 5 * 60, 14 * 86400

CHECKS = ("json-valid", "kql-present", "kind", "severity", "schedule", "trigger", "tactics",
          "techniques", "alert-details", "entities", "custom-details", "grouping",
          "unique-name", "substitutions", "watchlist-exists")
LEVELS = ("error", "warn", "off")

RE_DURATION = re.compile(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$")
RE_PLACEHOLDER = re.compile(r"\$\{(\w+)\}")
RE_COLUMN = re.compile(r"\{\{([^{}]*)\}\}")
RE_WATCHLIST = re.compile(r"_GetWatchlist\(\s*['\"]([^'\"]+)['\"]")
RE_ALIAS = re.compile(r"""(?:\balias\s*=\s*"([^"]+)"|"watchlistAlias"\s*:\s*"([^"]+)")""")


def seconds(duration):
    m = RE_DURATION.match(duration or "")
    if not m or duration in ("P", "PT"):
        return None
    d, h, mi, s = (int(x or 0) for x in m.groups())
    return ((d * 24 + h) * 60 + mi) * 60 + s


def glob_re(pattern: str):
    out, i = "", 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out, i = out + "(?:.*/)?", i + 3
        elif pattern.startswith("**", i):
            out, i = out + ".*", i + 2
        elif pattern[i] == "*":
            out, i = out + "[^/]*", i + 1
        else:
            out, i = out + re.escape(pattern[i]), i + 1
    return re.compile(out + r"\Z")


class Rule:
    """One analytic rule, whichever format it came from."""

    def __init__(self, path, kind, props, query, text, placeholders=()):
        self.path, self.kind, self.props, self.query = path, kind, props, query
        self.text, self.placeholders = text, set(placeholders)

    def line(self, key):
        """The line where the key first appears in the file, or 1."""
        pos = self.text.find(f'"{key}"')
        return self.text.count("\n", 0, pos) + 1 if pos >= 0 else 1

    @property
    def name(self):
        return self.props.get("displayName") or os.path.basename(self.path)


def tracked(root):
    out = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True, text=True,
                         check=True).stdout
    return sorted(out.splitlines())


def substitution_keys(path):
    """The keys of `substitutions = { ... }` in a terragrunt.hcl."""
    text = open(path, encoding="utf-8").read()
    m = re.search(r"\bsubstitutions\s*=\s*\{", text)
    if not m:
        return set()
    depth, i, keys = 1, m.end(), set()
    line_start = True
    while i < len(text) and depth:
        c = text[i]
        if c == "{" or c == "(" or c == "[":
            depth += 1
        elif c in "})]":
            depth -= 1
        elif c == "\n":
            line_start = True
            i += 1
            continue
        if line_start and depth == 1:
            km = re.match(r"\s*([A-Za-z_]\w*)\s*=", text[i:])
            if km:
                keys.add(km.group(1))
        line_start = False
        i += 1
    return keys


def load_rules(config, root, files, report):
    rules = []
    for source in config.get("rules") or []:
        base = source["path"].rstrip("/") + "/"
        fmt = source.get("format", "pair")
        subs = None
        if fmt == "pair" and source.get("substitutions_from"):
            subs = substitution_keys(os.path.join(root, source["substitutions_from"]))
        for path in files:
            if not path.startswith(base) or not path.endswith(".json"):
                continue
            full = os.path.join(root, path)
            text = open(full, encoding="utf-8-sig").read()
            if fmt == "pair":
                try:
                    data = json.loads(RE_PLACEHOLDER.sub("0", text), strict=False)
                except ValueError as e:
                    report.add("json-valid", path, 1, os.path.basename(path), f"not valid JSON: {e}")
                    continue
                kql_path = full[:-5] + ".kql"
                query = open(kql_path, encoding="utf-8").read() if os.path.exists(kql_path) else None
                found = set(RE_PLACEHOLDER.findall(text)) | set(RE_PLACEHOLDER.findall(query or ""))
                rule = Rule(path, data.get("kind"), data.get("properties") or {}, query, text, found)
                rule.substitutions = subs
                rule.query_path = kql_path[len(root) + 1:] if query is not None else None
                rules.append(rule)
            else:
                try:
                    data = json.loads(text, strict=False)
                except ValueError as e:
                    report.add("json-valid", path, 1, os.path.basename(path), f"not valid JSON: {e}")
                    continue
                for res in data.get("resources") or []:
                    if not str(res.get("type", "")).endswith("alertRules"):
                        continue
                    props = res.get("properties") or {}
                    rule = Rule(path, res.get("kind"), props, props.get("query"), text)
                    rule.substitutions = None
                    rule.query_path = None
                    rules.append(rule)
    return rules


class Report:
    def __init__(self):
        self.items = []

    def add(self, check, path, line, rule, message):
        self.items.append({"check": check, "path": path, "line": line, "rule": rule,
                           "message": message})


def lint(rule, report, aliases):
    p, add = rule.props, (lambda check, key, msg: report.add(check, rule.path, rule.line(key), rule.name, msg))
    scheduled = rule.kind in ("Scheduled", "NRT")

    if rule.kind not in KINDS:
        add("kind", "kind", f"kind {rule.kind!r} is not one Sentinel knows")
    if scheduled and not (rule.query or "").strip():
        add("kql-present", "displayName", "no query" + (" (the .kql beside it is missing)"
                                                        if rule.query is None else ""))
    if "severity" in p and p["severity"] not in SEVERITIES:
        add("severity", "severity", f"severity {p['severity']!r} is not one of {', '.join(sorted(SEVERITIES))}")

    if rule.kind == "Scheduled":
        freq, period = seconds(p.get("queryFrequency")), seconds(p.get("queryPeriod"))
        for key, value in (("queryFrequency", freq), ("queryPeriod", period)):
            if value is None:
                add("schedule", key, f"{key} {p.get(key)!r} is not an ISO 8601 duration")
            elif not MIN_SCHEDULE <= value <= MAX_SCHEDULE:
                add("schedule", key, f"{key} {p.get(key)} is outside 5 minutes to 14 days")
        if freq and period and freq > period:
            add("schedule", "queryFrequency",
                f"runs every {p['queryFrequency']} but looks back only {p['queryPeriod']}: "
                "events in between are never seen")
        if p.get("suppressionEnabled") and seconds(p.get("suppressionDuration")) is None:
            add("schedule", "suppressionDuration",
                f"suppressionDuration {p.get('suppressionDuration')!r} is not an ISO 8601 duration")
        if p.get("triggerOperator") not in OPERATORS:
            add("trigger", "triggerOperator", f"triggerOperator {p.get('triggerOperator')!r} is not one of "
                + ", ".join(sorted(OPERATORS)))
        threshold = p.get("triggerThreshold")
        if not isinstance(threshold, int) or isinstance(threshold, bool) or threshold < 0:
            add("trigger", "triggerThreshold", f"triggerThreshold {threshold!r} is not a whole number from 0")

    for tactic in p.get("tactics") or []:
        if tactic not in TACTICS:
            add("tactics", "tactics", f"tactic {tactic!r} is not one Sentinel knows")
    techniques = p.get("techniques") or []
    for technique in techniques:
        if not re.fullmatch(r"T\d{4}", str(technique)):
            add("techniques", "techniques", f"technique {technique!r} is not T plus four digits "
                "(a sub-technique belongs in subTechniques)")
    for sub in p.get("subTechniques") or []:
        if not re.fullmatch(r"T\d{4}\.\d{3}", str(sub)):
            add("techniques", "subTechniques", f"sub-technique {sub!r} is not Tdddd.ddd")
        elif sub.split(".")[0] not in techniques:
            add("techniques", "subTechniques", f"sub-technique {sub} without its technique {sub.split('.')[0]}")

    override = p.get("alertDetailsOverride") or {}
    for key in ("alertDisplayNameFormat", "alertDescriptionFormat"):
        text = override.get(key)
        if not text:
            continue
        stripped = RE_COLUMN.sub("", text)
        if "{{" in text and ("{" in stripped or "}" in stripped):
            add("alert-details", key, f"{key} has a broken {{{{column}}}}: {text.strip()[:80]!r}")
        columns = set(RE_COLUMN.findall(text))
        if len(columns) > MAX_PARAMS:
            add("alert-details", key, f"{key} uses {len(columns)} columns; Sentinel takes at most {MAX_PARAMS}")

    mappings = p.get("entityMappings") or []
    if len(mappings) > MAX_ENTITIES:
        add("entities", "entityMappings", f"{len(mappings)} entity mappings; Sentinel takes at most {MAX_ENTITIES}")
    for m in mappings:
        etype, fields = m.get("entityType"), m.get("fieldMappings") or []
        if etype not in ENTITY_TYPES:
            add("entities", "entityMappings", f"entity type {etype!r} is not one Sentinel knows")
        if not 1 <= len(fields) <= MAX_FIELDS:
            add("entities", "entityMappings", f"{etype} maps {len(fields)} identifiers; "
                f"Sentinel takes 1 to {MAX_FIELDS}")

    custom = p.get("customDetails") or {}
    if len(custom) > MAX_CUSTOM:
        add("custom-details", "customDetails", f"{len(custom)} custom details; Sentinel takes at most {MAX_CUSTOM}")

    grouping = ((p.get("incidentConfiguration") or {}).get("groupingConfiguration") or {})
    if grouping.get("enabled"):
        by_entities = grouping.get("groupByEntities") or []
        by_details = grouping.get("groupByAlertDetails") or []
        by_custom = grouping.get("groupByCustomDetails") or []
        if grouping.get("matchingMethod") == "Selected" and not (by_entities or by_details or by_custom):
            add("grouping", "matchingMethod", "matchingMethod Selected with nothing to group by")
        mapped = {m.get("entityType") for m in mappings}
        for e in by_entities:
            if e not in mapped:
                add("grouping", "groupByEntities", f"groups by {e}, which the rule does not map")
        for d in by_details:
            if d not in ALERT_DETAILS:
                add("grouping", "groupByAlertDetails", f"groups by alert detail {d!r}; only DisplayName and Severity")
        for c in by_custom:
            if c not in custom:
                add("grouping", "groupByCustomDetails", f"groups by custom detail {c}, which the rule does not define")

    if rule.substitutions is not None:
        for name in sorted(rule.placeholders - rule.substitutions):
            # Spelled out: the two characters together would start an
            # expression in the workflow this script is carried in.
            add("substitutions", "displayName", "$" + "{" + name + "} is not in the substitutions of the unit")

    if aliases is not None:
        for alias in sorted(set(RE_WATCHLIST.findall(rule.query or ""))):
            if alias not in aliases:
                report.add("watchlist-exists", rule.query_path or rule.path, 1, rule.name,
                           f"_GetWatchlist('{alias}'): no such watchlist in the code")


def check(config, root):
    errors = []
    levels = config.get("checks") or {}
    for name, level in levels.items():
        if name not in CHECKS:
            errors.append(f"unknown check {name!r}; known: {', '.join(CHECKS)}")
        elif level not in LEVELS:
            errors.append(f"check {name}: level must be one of {', '.join(LEVELS)}")
    exceptions = config.get("exceptions") or []
    for k, e in enumerate(exceptions):
        if not str(e.get("reason", "")).strip():
            errors.append(f"exception {k + 1} ({e.get('check')}, {e.get('rule')}) has no reason")
        if e.get("check") not in CHECKS:
            errors.append(f"exception {k + 1} names an unknown check {e.get('check')!r}")
    if not config.get("rules"):
        errors.append("rules: no path to analytic rules given")
    if errors:
        return [], errors, 0

    files = tracked(root)
    report = Report()
    rules = load_rules(config, root, files, report)
    aliases = None
    if config.get("watchlists"):
        aliases = set()
        for path in files:
            if any(glob_re(g).match(path) for g in config["watchlists"]):
                text = open(os.path.join(root, path), encoding="utf-8-sig").read()
                aliases |= {a or b for a, b in RE_ALIAS.findall(text)}
    names = {}
    for rule in rules:
        lint(rule, report, aliases)
        names.setdefault(rule.name, []).append(rule)
    for name, same in names.items():
        if len(same) > 1:
            for rule in same:
                report.add("unique-name", rule.path, rule.line("displayName"), name,
                           f"{len(same)} rules are called {name!r}")

    out = []
    for v in report.items:
        v["level"] = levels.get(v["check"], "error")
        if v["level"] == "off":
            continue
        v["exception"] = next((e["reason"] for e in exceptions
                               if e["check"] == v["check"] and glob_re(e.get("rule", "**")).match(v["path"])),
                              None)
        out.append(v)
    return out, errors, len(rules)


def escape(text):
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def main(config_path, root="."):
    with open(config_path, encoding="utf-8") as fh:
        config = json.load(fh) or {}
    violations, errors, count = check(config, root)
    for e in errors:
        print(f"::error title=sentinel-lint::{escape(e)}")
    failing = 0
    for v in violations:
        if v["exception"]:
            continue
        kind = "error" if v["level"] == "error" else "warning"
        failing += kind == "error"
        print(f"::{kind} file={v['path']},line={v['line']},title={v['check']}::{escape(v['message'])}")
    active = [v for v in violations if not v["exception"]]
    accepted = [v for v in violations if v["exception"]]
    out = ["## Sentinel content", "", f"{count} analytic rules checked."]
    for title, rows, extra in (("Findings", active, "level"), ("Accepted", accepted, "exception")):
        if rows:
            out += ["", f"### {title}", "", f"| Check | Rule | What | {extra.capitalize()} |", "|---|---|---|---|"]
            out += [f"| {v['check']} | `{v['path']}:{v['line']}` | {v['message'].replace('|', '/')} "
                    f"| {str(v[extra]).replace('|', '/')} |" for v in rows]
    if errors:
        out += ["", "### Config errors", ""] + [f"- {e}" for e in errors]
    text = "\n".join(out) + "\n"
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as fh:
            fh.write(text)
    else:
        print(text)
    return 1 if failing or errors else 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:3]))
