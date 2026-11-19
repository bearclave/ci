#!/usr/bin/env python3
"""Runs verify-apply.py over hand-made plans, logs and Activity Log answers.

The check block warnings have the shape terraform prints them in a
`terragrunt run --all` log; the plans carry only the fields the script reads.

Run: python3 tests/terragrunt/test_verify_apply.py
"""
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "src" / "terragrunt" / "verify-apply.py"

APPLY_LOG = """\
06:28:59.336 STDOUT [web/site] terraform: Apply complete! Resources: 0 added, 1 changed, 0 destroyed.
06:28:59.336 STDOUT [web/site] terraform: Warning: Check block assertion failed
06:28:59.336 STDOUT [web/site] terraform:   on main.tf line 7, in check "https":
06:28:59.336 STDOUT [web/site] terraform:    7:     condition     = data.http.site.status_code == 200
06:28:59.336 STDOUT [web/site] terraform:     ├────────────────
06:28:59.336 STDOUT [web/site] terraform:     │ data.http.site.status_code is 503
06:28:59.337 STDOUT [web/site] terraform: The site does not answer with 200.
06:28:59.400 STDOUT [web/db] terraform: Apply complete! Resources: 1 added, 0 changed, 0 destroyed.
"""

REPLAN_ERROR_LOG = """\
06:30:07.056 STDERR [web/db] terraform: Error: Unsupported argument
06:30:07.057 STDERR [web/db] terraform:   on extra.tf line 8, in resource "random_id" "x":
"""


def plan(changes=(), outputs=None, checks=(), subscription=None):
    p = {"resource_changes": [{"address": a, "change": {"actions": acts}} for a, acts in changes],
         "output_changes": outputs or {}, "checks": list(checks)}
    if subscription:
        p["configuration"] = {"provider_config": {"azurerm": {
            "name": "azurerm", "expressions": {"subscription_id": {"constant_value": subscription}}}}}
    return p


def check(name, status, message=None):
    inst = {"address": {"to_display": f"check.{name}"}, "status": status}
    if message:
        inst["problems"] = [{"message": message}]
    return {"address": {"kind": "check", "name": name, "to_display": f"check.{name}"},
            "status": status, "instances": [inst]}


class VerifyApply(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.json_dir = self.tmp / "json"

    def write_plan(self, unit, p):
        f = self.json_dir / unit / "tfplan.json"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(p))

    def write(self, name, text):
        f = self.tmp / name
        f.write_text(text)
        return str(f)

    def run_script(self, *args):
        return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True)

    def report(self, rc, activity, apply_log="", replan_log=""):
        out = self.tmp / "verification.json"
        r = self.run_script("report", "--replan-rc", str(rc), "--json-dir", str(self.json_dir),
                            "--replan-log", replan_log, "--apply-log", apply_log,
                            "--activity", self.write("activity.json", json.dumps(activity)),
                            "--out", str(out))
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(out.read_text()), r.stdout

    def test_clean_apply_passes(self):
        self.write_plan("web/db", plan([("terraform_data.a", ["no-op"])], checks=[check("ok", "pass")],
                                       subscription="sub-1"))
        v, md = self.report(0, {"subscriptions": ["sub-1"], "start": "s", "end": "e", "events": [], "errors": []})
        self.assertEqual(v["result"], "passed")
        self.assertEqual(v["replan"]["result"], "no-changes")
        self.assertEqual(v["activity_log"]["result"], "passed")
        self.assertEqual(v["checks"], {"result": "passed", "failed": [], "passed": 1, "unknown": 0})
        self.assertTrue(md.startswith("### Verification\n"))

    def test_remaining_change_fails(self):
        self.write_plan("web/db", plan([("terraform_data.a", ["update"])]))
        self.write_plan("web/site", plan([("terraform_data.b", ["read"])]))
        v, md = self.report(2, {"skipped": "no applied unit deploys to an Azure subscription"})
        self.assertEqual(v["result"], "failed")
        self.assertEqual(v["replan"]["units_with_changes"], ["web/db"])
        self.assertEqual(v["activity_log"]["result"], "skipped")
        self.assertIn("`web/db`", md)

    def test_change_found_even_when_exit_code_says_none(self):
        self.write_plan("web/db", plan(outputs={"x": {"actions": ["create"]}}))
        v, _ = self.report(0, {"skipped": "none"})
        self.assertEqual(v["replan"]["result"], "changes")
        self.assertEqual(v["result"], "failed")

    def test_replan_error_lists_errors(self):
        v, md = self.report(1, {"skipped": "none"}, replan_log=self.write("replan.txt", REPLAN_ERROR_LOG))
        self.assertEqual(v["replan"]["result"], "error")
        self.assertEqual(v["replan"]["errors"], ["web/db: Unsupported argument"])
        self.assertIn("web/db: Unsupported argument", md)

    def test_failed_azure_operation_fails(self):
        event = {"eventTimestamp": "2026-11-19T06:00:00Z",
                 "operationName": {"value": "Microsoft.Storage/storageAccounts/write"},
                 "resourceId": "/subscriptions/sub-1/resourceGroups/rg/providers/Microsoft.Storage/storageAccounts/st",
                 "status": {"value": "Failed"}, "subStatus": {"value": "Conflict"},
                 "properties": {"statusMessage": json.dumps({"error": {"message": "already exists"}})}}
        v, md = self.report(0, {"subscriptions": ["sub-1"], "start": "s", "end": "e", "events": [event], "errors": []})
        self.assertEqual(v["result"], "failed")
        self.assertEqual(v["activity_log"]["failures"][0]["message"], "already exists")
        self.assertIn("Microsoft.Storage/storageAccounts/write", md)

    def test_unreadable_log_does_not_fail(self):
        v, _ = self.report(0, {"subscriptions": ["sub-1"], "start": "s", "end": "e", "events": [],
                               "errors": ["sub-1: AuthorizationFailed"]})
        self.assertEqual(v["activity_log"]["result"], "unreadable")
        self.assertEqual(v["result"], "passed")

    def test_missing_activity_file_is_unreadable(self):
        out = self.tmp / "v.json"
        r = self.run_script("report", "--replan-rc", "skipped", "--json-dir", str(self.json_dir),
                            "--activity", str(self.tmp / "missing.json"), "--out", str(out))
        self.assertEqual(r.returncode, 0, r.stderr)
        v = json.loads(out.read_text())
        self.assertEqual(v["activity_log"]["result"], "unreadable")
        self.assertEqual(v["replan"]["result"], "skipped")
        self.assertEqual(v["checks"]["result"], "none")
        self.assertEqual(v["result"], "passed")

    def test_failed_check_from_apply_log_and_plan(self):
        self.write_plan("web/site", plan(checks=[check("https", "fail", "The site does not answer with 200.")]))
        self.write_plan("web/db", plan(checks=[check("backup", "fail", "No backup policy."),
                                               check("tls", "unknown")]))
        v, md = self.report(0, {"skipped": "none"}, apply_log=self.write("apply.txt", APPLY_LOG))
        self.assertEqual(v["result"], "failed")
        failed = {(c["unit"], c["check"]): c for c in v["checks"]["failed"]}
        # The same check from the log and the plan is reported once.
        self.assertEqual(set(failed), {("web/site", "https"), ("web/db", "backup")})
        self.assertEqual(v["checks"]["unknown"], 1)
        self.assertIn("check `backup`: No backup policy.", md)

    def test_check_from_log_only(self):
        v, _ = self.report(0, {"skipped": "none"}, apply_log=self.write("apply.txt", APPLY_LOG))
        self.assertEqual(v["checks"]["failed"], [{"unit": "web/site", "check": "https",
                                                  "message": "The site does not answer with 200.",
                                                  "source": "apply"}])

    def test_subscriptions(self):
        self.write_plan("a", plan(subscription="sub-1"))
        self.write_plan("b", plan(subscription="sub-2"))
        self.write_plan("c", plan(subscription="sub-1"))
        self.write_plan("d", plan())
        r = self.run_script("subscriptions", str(self.json_dir))
        self.assertEqual(r.stdout.split(), ["sub-1", "sub-2"])
        r = self.run_script("subscriptions", str(self.tmp / "none"))
        self.assertEqual(r.stdout.strip(), "")

    def test_checks_command_exit_code(self):
        self.write_plan("a", plan(checks=[check("x", "pass")]))
        self.assertEqual(self.run_script("checks", str(self.json_dir)).returncode, 0)
        self.write_plan("b", plan(checks=[check("y", "fail", "bad")]))
        r = self.run_script("checks", str(self.json_dir))
        self.assertEqual(r.returncode, 3)
        self.assertIn('::error title=check block failed::b: check "y": bad', r.stdout)


if __name__ == "__main__":
    unittest.main()
