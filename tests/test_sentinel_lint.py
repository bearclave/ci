#!/usr/bin/env python3
"""Tests of src/sentinel_lint.py over rules in every format.

Run: python3 -m unittest discover -s tests
"""
import copy
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import sentinel_lint  # noqa: E402

GOOD = {
    "kind": "Scheduled",
    "properties": {
        "displayName": "Break glass sign-in",
        "description": "A break glass account signed in.",
        "severity": "High",
        "enabled": True,
        "queryFrequency": "PT1H",
        "queryPeriod": "PT1H",
        "triggerOperator": "GreaterThan",
        "triggerThreshold": 0,
        "suppressionDuration": "PT5H",
        "suppressionEnabled": False,
        "tactics": ["InitialAccess", "Persistence"],
        "techniques": ["T1078"],
        "subTechniques": ["T1078.004"],
        "alertDetailsOverride": {"alertDisplayNameFormat": "Sign-in by {{Account}}",
                                 "alertDescriptionFormat": "{{Account}} from {{IP}} in ${state_account_name}"},
        "entityMappings": [{"entityType": "Account", "fieldMappings": [{"identifier": "Name", "columnName": "Account"}]},
                           {"entityType": "IP", "fieldMappings": [{"identifier": "Address", "columnName": "IP"}]}],
        "customDetails": {"Country": "Location"},
        "incidentConfiguration": {"createIncident": True, "groupingConfiguration": {
            "enabled": True, "matchingMethod": "Selected", "groupByEntities": ["Account"],
            "groupByAlertDetails": ["DisplayName"], "groupByCustomDetails": ["Country"]}},
    },
}
QUERY = "SigninLogs\n| where UserPrincipalName in (_GetWatchlist('BreakGlassAccounts') | project SearchKey)\n"
UNIT = 'inputs = {\n  substitutions = {\n    state_account_name = "st"\n    other = join(",", ["a"])\n  }\n}\n'
WATCHLISTS = 'inputs = {\n  watchlists = {\n    "x" = {\n      alias = "BreakGlassAccounts"\n    }\n  }\n}\n'
CONFIG = {"rules": [{"path": "rules", "format": "pair", "substitutions_from": "unit/terragrunt.hcl"}],
          "watchlists": ["wl/terragrunt.hcl"]}


class Lint(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        self.write("unit/terragrunt.hcl", UNIT)
        self.write("wl/terragrunt.hcl", WATCHLISTS)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, path, text):
        p = self.root / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        subprocess.run(["git", "add", "-A"], cwd=self.root, check=True)

    def rule(self, name="good", change=None, query=QUERY):
        data = copy.deepcopy(GOOD)
        if change:
            change(data["properties"])
        self.write(f"rules/{name}.json", json.dumps(data, indent=2))
        if query is not None:
            self.write(f"rules/{name}.kql", query)

    def found(self, config=CONFIG):
        violations, errors, count = sentinel_lint.check(config, str(self.root))
        self.assertEqual(errors, [])
        return sorted(v["check"] for v in violations if not v["exception"])

    def test_good_rule(self):
        self.rule()
        self.assertEqual(self.found(), [])

    def test_broken_column(self):
        self.rule(change=lambda p: p["alertDetailsOverride"].update(alertDisplayNameFormat="Group {{{Name}} found"))
        self.assertEqual(self.found(), ["alert-details"])

    def test_too_many_columns(self):
        self.rule(change=lambda p: p["alertDetailsOverride"].update(
            alertDisplayNameFormat="{{A}} {{B}} {{C}} {{D}}"))
        self.assertEqual(self.found(), ["alert-details"])

    def test_unknown_tactic(self):
        self.rule(change=lambda p: p.update(tactics=["Evasion"]))
        self.assertEqual(self.found(), ["tactics"])

    def test_techniques(self):
        self.rule(change=lambda p: p.update(techniques=["T1078.004"], subTechniques=["T1110.003"]))
        self.assertEqual(self.found(), ["techniques", "techniques"])

    def test_frequency_longer_than_period(self):
        self.rule(change=lambda p: p.update(queryFrequency="P1D", queryPeriod="PT1H"))
        self.assertEqual(self.found(), ["schedule"])

    def test_period_beyond_fourteen_days_and_bad_duration(self):
        self.rule(change=lambda p: p.update(queryPeriod="P15D", queryFrequency="1h"))
        self.assertEqual(self.found(), ["schedule", "schedule"])

    def test_trigger(self):
        self.rule(change=lambda p: p.update(triggerOperator="Above", triggerThreshold=-1))
        self.assertEqual(self.found(), ["trigger", "trigger"])

    def test_entities(self):
        def change(p):
            p["entityMappings"] = [{"entityType": "User", "fieldMappings": []}] + \
                [{"entityType": "IP", "fieldMappings": [{"identifier": "Address", "columnName": "IP"}]}] * 10
            p["incidentConfiguration"]["groupingConfiguration"]["groupByEntities"] = []
        self.rule(change=change)
        self.assertEqual(self.found(), ["entities", "entities", "entities"])

    def test_grouping(self):
        def change(p):
            g = p["incidentConfiguration"]["groupingConfiguration"]
            g.update(groupByEntities=["Host"], groupByAlertDetails=["Title"], groupByCustomDetails=["Nope"])
        self.rule(change=change)
        self.assertEqual(self.found(), ["grouping", "grouping", "grouping"])

    def test_selected_with_nothing(self):
        def change(p):
            g = p["incidentConfiguration"]["groupingConfiguration"]
            g.update(groupByEntities=[], groupByAlertDetails=[], groupByCustomDetails=[])
        self.rule(change=change)
        self.assertEqual(self.found(), ["grouping"])

    def test_missing_query(self):
        self.rule(query=None)
        self.assertEqual(self.found(), ["kql-present"])

    def test_unknown_substitution(self):
        self.rule(change=lambda p: p.update(description="in ${missing}"))
        self.assertEqual(self.found(), ["substitutions"])

    def test_unknown_watchlist(self):
        self.rule(query="X | where a in (_GetWatchlist(\"Nowhere\") | project SearchKey)")
        self.assertEqual(self.found(), ["watchlist-exists"])

    def test_duplicate_name(self):
        self.rule("one")
        self.rule("two")
        self.assertEqual(self.found(), ["unique-name", "unique-name"])

    def test_severity_and_kind(self):
        def change(p):
            p["severity"] = "Critical"
        self.rule(change=change)
        self.assertEqual(self.found(), ["severity"])

    def test_invalid_json(self):
        self.write("rules/bad.json", "{ nope")
        self.write("rules/bad.kql", "X")
        self.assertEqual(self.found(), ["json-valid"])

    def test_arm_format(self):
        props = copy.deepcopy(GOOD["properties"])
        props["query"] = QUERY
        props["tactics"] = ["Evasion"]
        props["description"] = "no placeholders here"
        props["alertDetailsOverride"]["alertDescriptionFormat"] = "{{Account}}"
        arm = {"resources": [{"type": "Microsoft.OperationalInsights/workspaces/providers/alertRules",
                              "kind": "Scheduled", "properties": props},
                             {"type": "Microsoft.Something/else"}]}
        self.write("ARM/rule.json", "﻿" + json.dumps(arm))
        config = {"rules": [{"path": "ARM", "format": "arm"}]}
        self.assertEqual(self.found(config), ["tactics"])

    def yaml_rule(self, text, config=None):
        self.write("account.hcl", 'locals {\n  principals = {\n    writers = ["a"]\n  }\n}\n')
        self.write("wl/definitions/break-glass.yaml", "alias: BreakGlassAccounts\ndisplayName: x\n")
        self.write("rules/definitions/sign-in.yaml", text)
        return self.found(config or {"rules": [{"path": "rules/definitions", "format": "yaml",
                                                "substitutions_from": ["unit/terragrunt.hcl", "account.hcl#principals"]}],
                                     "watchlists": ["wl/definitions/*.yaml"]})

    YAML = (
        "name: Break glass sign-in\nkind: Scheduled\nseverity: High\nqueryFrequency: 1h\nqueryPeriod: 1d\n"
        "triggerOperator: gt\ntriggerThreshold: 0\nsuppressionEnabled: false\nsuppressionDuration: 5h\n"
        "tactics: [InitialAccess]\nrelevantTechniques: [T1078, T1078.004]\n"
        "query: |\n  SigninLogs | where U in (_GetWatchlist('BreakGlassAccounts') | project SearchKey)\n"
        "  | where A in (${writers}) and S == '${state_account_name}'\n"
        "incidentConfiguration:\n  createIncident: true\n  groupingConfiguration:\n    enabled: true\n"
        "    matchingMethod: AnyAlert\n    lookbackDuration: 1d\n")

    def test_yaml_format(self):
        self.assertEqual(self.yaml_rule(self.YAML), [])

    def test_yaml_format_findings(self):
        text = (self.YAML.replace("queryFrequency: 1h", "queryFrequency: 2d").replace("gt", "above")
                .replace("[T1078, T1078.004]", "[T1078.004]").replace("BreakGlassAccounts')", "Nowhere')")
                .replace("${writers}", "${missing}"))
        self.assertEqual(self.yaml_rule(text),
                         ["schedule", "substitutions", "trigger", "watchlist-exists"])

    def test_yaml_line_and_invalid(self):
        self.yaml_rule(self.YAML.replace("tactics: [InitialAccess]", "tactics: [Evasion]"))
        violations, _, _ = sentinel_lint.check({"rules": [{"path": "rules/definitions", "format": "yaml"}]},
                                               str(self.root))
        self.assertEqual([(v["check"], v["line"]) for v in violations], [("tactics", 10)])
        self.write("rules/definitions/bad.yaml", "name: [unclosed\n")
        violations, _, _ = sentinel_lint.check({"rules": [{"path": "rules/definitions", "format": "yaml"}]},
                                               str(self.root))
        self.assertIn("json-valid", [v["check"] for v in violations])

    def test_levels_and_exceptions(self):
        self.rule(change=lambda p: p.update(tactics=["Evasion"], techniques=["T1"]))
        config = {**CONFIG, "checks": {"techniques": "off"},
                  "exceptions": [{"check": "tactics", "rule": "rules/*.json", "reason": "decided later"}]}
        violations, errors, _ = sentinel_lint.check(config, str(self.root))
        self.assertEqual(errors, [])
        self.assertEqual([(v["check"], bool(v["exception"])) for v in violations], [("tactics", True)])

    def test_exceptions_in_the_common_format(self):
        self.rule(change=lambda p: p.update(tactics=["Evasion"]))
        config = {**CONFIG, "exceptions": [{"rule": "tactics", "path": "rules/*.json", "reason": "decided later",
                                            "review": "2027-05-19"}]}
        violations, errors, _ = sentinel_lint.check(config, str(self.root))
        self.assertEqual(errors, [])
        self.assertEqual([(v["check"], bool(v["exception"])) for v in violations], [("tactics", True)])

    def test_config_errors(self):
        _, errors, _ = sentinel_lint.check({"rules": [], "checks": {"nope": "error"},
                                            "exceptions": [{"check": "tactics"}]}, str(self.root))
        self.assertEqual(len(errors), 3)

    def test_durations(self):
        self.assertEqual(sentinel_lint.seconds("PT5M"), 300)
        self.assertEqual(sentinel_lint.seconds("P1DT2H"), 93600)
        self.assertIsNone(sentinel_lint.seconds("P"))
        self.assertIsNone(sentinel_lint.seconds("5m"))


if __name__ == "__main__":
    unittest.main()
