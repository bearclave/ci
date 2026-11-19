#!/usr/bin/env python3
"""Runs plan-summary.py and apply-summary.py over sample output and checks the comments.

Text samples are real `terragrunt run --all plan --no-color` output over units
made of terraform_data with local state, so they carry nothing from the tenant:

  multi-unit.txt  create, update in-place, destroy, replace, read, a tainted,
                  a moved and a forgotten resource, a sensitive value, one unit
                  failing and one without a change
  no-change.txt   three units, none with a change
  over-limit.txt  two large units whose diffs do not fit into one comment
  import.txt      a unit that only imports, one that imports and updates, and
                  one without a change
  move-forget.txt a unit that only moves a resource, one that only forgets
                  one (removed, destroy = false), and one without a change
  outputs-only.txt  a unit where only an output changes, and one without a change
  cancelled.txt   a run interrupted (SIGINT, as when GitHub cancels a job) while
                  one of three units was still in its before_hook
  destroy.txt     a destroy plan of two deleted units, the way the workflow
                  plans them from the base commit: one with two resources, one
                  whose state was already empty
  readable.txt    one unit, with readable/mg/rg-web/tfplan.json: its plan in
                  JSON, written by hand after terraform's format, for the cases
                  the real plans do not have (replace, sensitive, azapi, move)

Real plans of the pipeline, put through tools/sanitise-plan.py (GUIDs and
e-mail addresses replaced, sensitive values redacted, unchanged resources
without values) and plan.txt the same way:

  real/github/    iac-github: a repository description changes, the
                  organisation unit it depends on does not
  real/azure/     iac-azure: rg-security and its units, with a tag, a Key
                  Vault setting, a list and a removed role assignment

apply/ holds apply output written after terragrunt's format (an apply needs a
tenant): handbook.txt applies the real/github change, rg-web.txt applies the
readable.txt plan with one resource failing.

Run: python3 tests/terragrunt/test_plan_summary.py
"""
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
SRC = pathlib.Path(__file__).resolve().parents[2] / "src" / "terragrunt"
SCRIPT = SRC / "plan-summary.py"
APPLY = SRC / "apply-summary.py"
MAX = 60_000
RE_UNIT = re.compile(r"\[([^\]]+)\] terraform: ?")
ENV = {"GITHUB_RUN_ID": "4242", "GITHUB_REPOSITORY": "bearclave/iac-test",
       "GITHUB_SHA": "0123456789abcdef0123456789abcdef01234567",
       "GITHUB_SERVER_URL": "https://github.com"}


def run(text: str, *args: str, script=SCRIPT) -> dict:
    """Runs the script over the given output the way the workflows do."""
    with tempfile.TemporaryDirectory() as tmp:
        plan = pathlib.Path(tmp, "plan.txt")
        plan.write_text(text, encoding="utf-8")
        github_output = pathlib.Path(tmp, "github_output")
        github_output.touch()
        changes = pathlib.Path(tmp, "changes.json")
        args = [a.replace("{changes}", str(changes)) for a in args]
        env = {**os.environ, **ENV, "GITHUB_OUTPUT": str(github_output)}
        proc = subprocess.run(
            [sys.executable, str(script), str(plan), *args],
            capture_output=True, text=True, env=env, check=False,
        )
        return {
            "comment": proc.stdout.rstrip("\n"),
            "code": proc.returncode,
            "stderr": proc.stderr,
            "github_output": github_output.read_text(encoding="utf-8"),
            "changes": json.loads(changes.read_text()) if changes.exists() else None,
        }


def sample(name: str) -> str:
    return (HERE / name).read_text(encoding="utf-8")


def unit_lines(text: str, unit: str, stream: str = "STDOUT") -> list:
    """What terraform printed for one unit, without the terragrunt prefix."""
    out = []
    for line in text.splitlines():
        m = RE_UNIT.search(line)
        if m and m.group(1) == unit and line[:m.start()].split()[-1] == stream:
            out.append(line[m.end():])
    return out


def section(lines: list, start: str, end: str) -> list:
    i = lines.index(start)
    j = next(k for k in range(i, len(lines)) if lines[k].startswith(end))
    return lines[i + 1:j]


def diff_of(comment: str, unit: str) -> list:
    """The lines inside the ```diff block that belongs to a unit."""
    part = comment[comment.index(f"#### `{unit}`"):]
    part = part[part.index("```diff\n") + len("```diff\n"):]
    return part[:part.index("\n```")].split("\n")


def as_terraform(line: str) -> str:
    """Undoes moving the action symbol to the first column."""
    m = re.match(r"^([-+!])( +)(.*)$", line)
    if not m:
        return line
    sign = "~" if m.group(1) == "!" else m.group(1)
    return m.group(2)[1:] + sign + " " + m.group(3)


def interleave(text: str) -> str:
    """The same output with the units taking turns line by line."""
    queues, loose = {}, []
    for line in text.splitlines():
        m = RE_UNIT.search(line)
        (queues.setdefault(m.group(1), []) if m else loose).append(line)
    out = []
    while any(queues.values()):
        for q in queues.values():
            if q:
                out.append(q.pop(0))
    return "\n".join(out + loose) + "\n"


def rename(text: str, old: str, new: str) -> str:
    """The lines of one unit, handed to another unit."""
    lines = [line for line in text.splitlines() if f"[{old}] terraform:" in line]
    moved = [line.replace(f"[{old}] terraform:", f"[{new}] terraform:") for line in lines]
    return "\n".join(moved) + "\n"


def only(text: str, *units: str) -> str:
    """The output of the given units alone."""
    keep = [line for line in text.splitlines()
            if (m := RE_UNIT.search(line)) and m.group(1) in units]
    return "\n".join(keep) + "\n"


def diff_states(comment: str) -> dict:
    """For each unit in the details: whole, shortened or left out."""
    states = {}
    if "<details>" not in comment:
        return states
    details = comment[comment.index("<details>"):]
    for part in details.split("\n#### ")[1:]:
        unit = part.split("`")[1]
        if "_Left out, the comment would be too long._" in part.split("\n#### ")[0]:
            states[unit] = "left out"
        elif re.search(r"_\d+ of \d+ lines left out\._", part):
            states[unit] = "shortened"
        elif "```diff" in part:
            states[unit] = "whole"
    return states


def table(comment: str, heading: str) -> list:
    """The rows of the table under a heading, as lists of cells."""
    part = comment[comment.index(heading):].split("\n\n")
    rows = next(p for p in part[1:] if p.startswith("|")).split("\n")[2:]
    return [[c.strip() for c in re.split(r"(?<!\\)\|", r)[1:-1]] for r in rows]


class MultiUnit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = sample("multi-unit.txt")
        cls.result = run(cls.text, "--github-output", "--checks=formatting=success,validation=success")
        cls.comment = cls.result["comment"]

    def test_sections_come_in_order(self):
        heads = ["## ❌ Plan failed", "### ⚠️ Destroys and replaces", "### Changes",
                 "### ❌ Errors", "**Status:**", "**Needed:**", "<details>",
                 "**1 unit without a change:**", "_The raw plan is in artifact `plan-4242`"]
        positions = [self.comment.index(h) for h in heads]
        self.assertEqual(positions, sorted(positions))

    def test_heading_line(self):
        self.assertIn("\n`bearclave/iac-test` · [`0123456`](https://github.com/bearclave/iac-test/"
                      "commit/0123456789abcdef0123456789abcdef01234567) · 5 units · ➕ 2 · ✏️ 2 · "
                      "🗑️ 2 · ♻️ 2 · 🚚 1 moved · ⏏️ 1 forgotten\n", self.comment)

    def test_error_block_is_whole(self):
        block = unit_lines(self.text, "shared/vault", "STDERR")
        self.assertTrue(block[0].startswith("Error: "))
        self.assertGreater(len(block), 5)
        self.assertIn("**`shared/vault`**\n\n```\n" + "\n".join(block) + "\n```", self.comment)

    def test_destroys_and_replaces_first(self):
        rows = table(self.comment, "### ⚠️ Destroys and replaces")
        self.assertEqual([(r[0], r[2], r[3]) for r in rows], [
            ("🗑️", "`Reader on sub`", "`web/misc`"), ("🗑️", "`old`", "`web/site`"),
            ("♻️", "`a`", "`web/misc`"), ("♻️", "`cert`", "`web/site`")])
        self.assertEqual(rows[2][4], "tainted")

    def test_changes_from_the_text(self):
        rows = table(self.comment, "### Changes")
        self.assertEqual([(r[0], r[2].split("<br>")[0], r[3]) for r in rows], [
            ("✏️", "`db`", "updated, see the details"),
            ("✏️", "`site`", "updated, see the details"),
            ("➕", "`replica`", "created"), ("➕", "`cdn`", "created"),
            ("🚚", "`m2`", "moved from `terraform_data.m`"),
            ("⏏️", "`d`", "removed from Terraform, left in place")])
        self.assertIn("<sub>`web/db`</sub>", rows[0][2])
        self.assertNotIn("peer", self.comment.split("<details>")[0])

    def test_needed_lists_the_fix_and_the_approval(self):
        self.assertIn("**Needed:**\n\n- fix the errors above", self.comment)
        self.assertIn("- approval of the destroy: an approving review of this pull request "
                      "after this plan (`destroy / approved`), then the merge applies it",
                      self.comment)

    def test_diff_is_what_terraform_printed(self):
        for unit in ["web/site", "web/misc", "web/db"]:
            lines = unit_lines(self.text, unit)
            expected = section(lines, "Terraform will perform the following actions:", "Plan: ")
            if "Changes to Outputs:" in lines:
                expected += ["Changes to Outputs:"] + [
                    x for x in lines[lines.index("Changes to Outputs:") + 1:] if x[:1].isspace()]
            shown = [as_terraform(x) for x in diff_of(self.comment, unit) if x]
            self.assertEqual(shown, expected, unit)

    def test_details_folded_and_coloured(self):
        self.assertEqual(self.comment.count("<details><summary>Details per unit</summary>"), 1)
        diff = diff_of(self.comment, "web/db")
        self.assertIn("!           size = 10 -> 20", diff)
        self.assertIn('+           zone = "2"', diff)

    def test_sensitive_values_stay_masked(self):
        masked = "(sensitive value)"
        self.assertEqual(self.comment.count(masked), self.text.count(masked))

    def test_prefix_is_gone(self):
        self.assertNotRegex(self.comment, r"\] terraform:")
        self.assertNotRegex(self.comment, r"\d\d:\d\d:\d\d\.\d{3}")

    def test_verdict(self):
        self.assertEqual(self.result["github_output"],
                         "has_changes=true\nhas_errors=true\nhas_destroys=true\n")
        drift = run(self.text, "--fail-on-change", "--drift")
        self.assertEqual(drift["code"], 1)
        self.assertIn("DRIFT: 2 to create, 2 to update, 2 to destroy, 2 to replace, 1 to move, "
                      "1 to forget, outputs change in 1 unit, 3 errors.", drift["stderr"])
        self.assertTrue(drift["comment"].startswith("## ❌ Drift check failed"))
        self.assertNotIn("**Needed:**", drift["comment"])

    def test_interleaving_does_not_matter(self):
        args = ("--checks=formatting=success,validation=success",)
        self.assertEqual(run(interleave(self.text), *args)["comment"], run(self.text, *args)["comment"])


class NoChange(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = run(sample("no-change.txt"), "--github-output", "--fail-on-change",
                         "--trivy=none", "--checks=formatting=success,validation=success")
        cls.comment = cls.result["comment"]

    def test_comment(self):
        self.assertEqual(self.comment, "\n".join([
            "## 📋 Plan complete",
            "",
            "`bearclave/iac-test` · [`0123456`](https://github.com/bearclave/iac-test/commit/"
            "0123456789abcdef0123456789abcdef01234567) · 3 units · ➕ 0 · ✏️ 0 · 🗑️ 0 · ♻️ 0",
            "",
            "### Changes",
            "",
            "None: the plan matches what is deployed.",
            "",
            "**Status:** 🛡️ no security findings · ✅ formatting and validation passed",
            "",
            "**Needed:** nothing; merging applies nothing.",
            "",
            "<details><summary>Details per unit</summary>",
            "",
            "**3 units without a change:** `tenant/groups`, `tenant/users`, `web/site`",
            "",
            "</details>",
            "",
            "_The raw plan is in artifact `plan-4242` (plan.txt)._",
        ]))

    def test_verdict(self):
        self.assertEqual(self.result["code"], 0)
        self.assertEqual(self.result["github_output"],
                         "has_changes=false\nhas_errors=false\nhas_destroys=false\n")


class WithoutAPlan(unittest.TestCase):
    def test_nothing_selected(self):
        comment = run("", "--nothing-selected")["comment"]
        self.assertTrue(comment.startswith("## 📋 Plan complete"))
        self.assertIn("Nothing to plan: the change selects no unit.", comment)

    def test_failed_check(self):
        comment = run("", "--no-plan", "--checks=formatting=failure,validation=skipped")["comment"]
        self.assertTrue(comment.startswith("## ❌ Plan failed"))
        self.assertIn("**Status:** ❌ formatting failed", comment)
        self.assertIn("No plan was produced, see the run log", comment)


class Import(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = sample("import.txt")
        cls.result = run(cls.text, "--github-output")
        cls.comment = cls.result["comment"]

    def test_counts(self):
        self.assertIn(" · 3 units · ➕ 0 · ✏️ 1 · 🗑️ 0 · ♻️ 0 · 📥 2 imported\n", self.comment)

    def test_import_without_a_change_is_a_count(self):
        self.assertIn("📥 **Imported without a change:** 1 — `tenant/users` (1).", self.comment)
        self.assertNotIn("| 📥 |", self.comment)

    def test_import_with_a_change_is_a_row(self):
        rows = table(self.comment, "### Changes")
        self.assertEqual([r[3] for r in rows], ["updated, see the details", "imported"])

    def test_import_alone_is_a_change(self):
        alone = only(self.text, "tenant/users", "shared/dns")
        result = run(alone, "--github-output")
        self.assertEqual(result["github_output"],
                         "has_changes=true\nhas_errors=false\nhas_destroys=false\n")
        drift = run(alone, "--fail-on-change")
        self.assertEqual(drift["code"], 1)
        self.assertIn("DRIFT: 0 to create, 0 to update, 0 to destroy, 1 to import, 0 errors.",
                      drift["stderr"])


class MoveForget(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = sample("move-forget.txt")
        cls.result = run(cls.text, "--github-output")
        cls.comment = cls.result["comment"]

    def test_rows(self):
        rows = table(self.comment, "### Changes")
        self.assertEqual([(r[0], r[3]) for r in rows], [
            ("🚚", "moved from `terraform_data.team`"),
            ("⏏️", "removed from Terraform, left in place")])

    def test_each_alone_is_a_change(self):
        for unit in ("tenant/groups", "tenant/users"):
            result = run(only(self.text, unit, "shared/dns"), "--github-output")
            self.assertEqual(result["github_output"],
                             "has_changes=true\nhas_errors=false\nhas_destroys=false\n", unit)


class OutputsOnly(unittest.TestCase):
    def test_outputs_are_not_rows(self):
        result = run(sample("outputs-only.txt"), "--github-output")
        self.assertIn("Only output values change, in `tenant/groups`.", result["comment"])
        self.assertNotIn("| | Resource |", result["comment"])
        self.assertEqual(result["github_output"],
                         "has_changes=true\nhas_errors=false\nhas_destroys=false\n")


class Cancelled(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = sample("cancelled.txt")
        cls.result = run(cls.text, "--github-output")
        cls.comment = cls.result["comment"]

    def test_unit_without_a_result_is_an_error(self):
        self.assertTrue(self.comment.startswith("## ❌ Plan failed"))
        self.assertIn("**Without a result:** `tenant/groups`", self.comment)
        self.assertIn("❌ 1 unit without a plan", self.comment)

    def test_verdict(self):
        self.assertEqual(self.result["github_output"],
                         "has_changes=true\nhas_errors=true\nhas_destroys=false\n")
        drift = run(self.text, "--fail-on-change")
        self.assertEqual(drift["code"], 1)
        self.assertIn(", 0 errors, 1 unit without a result.", drift["stderr"])


class Destroy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = run(sample("destroy.txt"), "--github-output")
        cls.comment = cls.result["comment"]

    def test_only_the_destroy_table(self):
        self.assertIn("### ⚠️ Destroys and replaces", self.comment)
        self.assertNotIn("### Changes", self.comment)
        self.assertIn(" · ➕ 0 · ✏️ 0 · 🗑️ 2 · ♻️ 0", self.comment)

    def test_empty_state_is_not_an_error(self):
        self.assertNotIn("Without a result", self.comment)
        self.assertIn("**1 unit without a change:** `tenant/old-empty`", self.comment)

    def test_needed_is_the_approval(self):
        self.assertIn("**Needed:** approval of the destroy", self.comment)

    def test_verdict(self):
        self.assertEqual(self.result["github_output"],
                         "has_changes=true\nhas_errors=false\nhas_destroys=true\n")


class Readable(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = run(sample("readable.txt"), f"--json-dir={HERE / 'readable'}",
                         "--changes-out={changes}")
        cls.comment = cls.result["comment"]
        cls.changes = table(cls.comment, "### Changes")
        cls.destroys = table(cls.comment, "### ⚠️ Destroys and replaces")

    def row(self, kind, what=None):
        rows = [r for r in self.changes if r[1] == kind and (what is None or r[3].startswith(what))]
        self.assertEqual(len(rows), 1, f"one {kind} row: {rows}")
        return rows[0]

    def test_destroy_says_why(self):
        self.assertEqual(self.destroys[0], [
            "🗑️", "Role assignment", "`Key Vault Secrets User for sp:sp-invoices on kv-app-fin`",
            "`mg/rg-web`", "its key is no longer in the `for_each`"])

    def test_replace_says_what_forces_it(self):
        self.assertEqual(self.destroys[1][:3], ["♻️", "Key Vault certificate", "`cert-web`"])
        self.assertEqual(self.destroys[1][4],
                         "setting `certificate_policy.x509_certificate_properties.subject` forces it: "
                         "`CN=old.example.cz` → `CN=new.example.cz`")

    def test_tag(self):
        self.assertEqual(self.row("Resource group")[2:], [
            "`rg-web-app-weu-001`", "tag `criticality`", "`medium`", "`high`"])

    def test_setting(self):
        self.assertEqual(self.row("Key Vault")[3:], [
            "setting `public_network_access_enabled`", "`true`", "`false`"])

    def test_sensitive_value_never_shown(self):
        self.assertEqual(self.row("Key Vault secret")[3:], [
            "setting `value`", "(sensitive)", "(sensitive)"])
        self.assertNotIn("s3cr3t", self.comment)
        self.assertNotIn("s3cr3t", json.dumps(self.result["changes"]))

    def test_list_added(self):
        self.assertEqual(self.row("Analytic rule", "setting `tactics`")[3],
                         "setting `tactics`: added `Persistence`")

    def test_known_after_apply_left_out(self):
        self.assertEqual(self.row("Static Web App")[3:], ["setting `sku_tier`", "`Free`", "`Standard`"])
        self.assertNotIn("etag", self.comment.split("<details>")[0])

    def test_azapi_by_its_type_and_display_name(self):
        row = self.row("Analytic rule", "setting `alertDetailsOverride")
        self.assertEqual(row[2], "`Ransomware group created`")
        self.assertIn("…as been detected. {{{ResultMessage}} by user", row[4])
        self.assertNotIn("`output`", self.comment.split("<details>")[0])

    def test_import_and_move(self):
        self.assertIn("📥 **Imported without a change:** 1 — `mg/rg-web` (1).", self.comment)
        self.assertEqual(self.row("Diagnostic settings")[3],
                         "moved from `azurerm_monitor_diagnostic_setting.this`")

    def test_data_sources_and_outputs_left_out(self):
        self.assertNotIn("client config", self.comment)
        self.assertNotIn("Outputs", self.comment.split("<details>")[0])

    def test_without_json_one_row_per_resource(self):
        plain = run(sample("readable.txt"))["comment"]
        self.assertIn("| ✏️ | Resource group | `this` | updated, see the details | — | — |", plain)

    def test_changes_json_holds_the_rows(self):
        entries = self.result["changes"]["entries"]
        rg = next(e for e in entries if e["kind"] == "Resource group")
        self.assertEqual(rg["rows"], [["tag `criticality`", "`medium`", "`high`"]])
        self.assertEqual(self.result["changes"]["units"], ["mg/rg-web"])

    def test_shards_export_what_the_merge_shows(self):
        """The merge over the exports reads as one job over the JSON plans would."""
        with tempfile.TemporaryDirectory() as tmp:
            trivy = pathlib.Path(tmp, "trivy")
            trivy.mkdir()
            report = {"Results": [{"Target": "mg/rg-web/tfplan.json", "Misconfigurations": [
                {"AVDID": "AVD-AZU-0013", "Title": "Key vault network ACL default action",
                 "Severity": "HIGH", "Status": "FAIL",
                 "CauseMetadata": {"Resource": "azurerm_key_vault.this"}}]}]}
            pathlib.Path(trivy, "mg_rg-web.json").write_text(json.dumps(report))
            whole = run(sample("readable.txt"), f"--json-dir={HERE / 'readable'}",
                        f"--trivy={trivy}")
            parts = pathlib.Path(tmp, "parts")
            parts.mkdir()
            run(sample("readable.txt"), f"--json-dir={HERE / 'readable'}",
                f"--export={parts / 'shard-0.json'}")
            run(sample("no-change.txt"), f"--trivy={trivy}", f"--export={parts / 'shard-1.json'}")
            merged = run(sample("readable.txt"), f"--import={parts}", "--trivy=")
            exported = json.loads((parts / "shard-0.json").read_text())
        self.assertEqual(merged["comment"], whole["comment"])
        self.assertEqual(merged["github_output"], whole["github_output"])
        # Only what the comment shows leaves a share, never the plan JSON.
        self.assertEqual(set(exported), {"entries", "outputs", "findings"})
        self.assertNotIn("s3cr3t", json.dumps(exported))

    def test_trivy_findings(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = {"Results": [{"Target": "mg/rg-web/tfplan.json", "Misconfigurations": [
                {"AVDID": "AVD-AZU-0013", "Title": "Key vault network ACL default action",
                 "Severity": "HIGH", "Status": "FAIL",
                 "CauseMetadata": {"Resource": "azurerm_key_vault.this"}},
                {"AVDID": "AVD-AZU-0016", "Title": "Purge protection", "Severity": "HIGH",
                 "Status": "PASS"}]}]}
            pathlib.Path(tmp, "mg_rg-web.json").write_text(json.dumps(report))
            comment = run(sample("readable.txt"), f"--trivy={tmp}")["comment"]
        self.assertIn("### 🛡️ Security findings", comment)
        self.assertIn("| `mg/rg-web` | HIGH | AVD-AZU-0013: Key vault network ACL default action "
                      "| `azurerm_key_vault.this` |", comment)
        self.assertNotIn("AVD-AZU-0016", comment)
        self.assertIn("**Status:** 🛡️ 1 security finding (below)", comment)
        self.assertIn("- fix the security findings or accept them", comment)


class RealGitHub(unittest.TestCase):
    """iac-github: one repository description changes."""

    @classmethod
    def setUpClass(cls):
        base = HERE / "real" / "github"
        cls.result = run((base / "plan.txt").read_text(encoding="utf-8"), "--github-output",
                         f"--json-dir={base / 'plan-json'}", "--trivy=none",
                         "--checks=formatting=success,validation=success", "--changes-out={changes}")
        cls.comment = cls.result["comment"]

    def test_one_row(self):
        self.assertEqual(table(self.comment, "### Changes"), [[
            "✏️", "Repository", "`handbook`", "setting `description`",
            "`Field notes and gotchas from Azure security work.`",
            "`Field notes and gotchas from Azure security work (test).`"]])

    def test_counts_and_needed(self):
        self.assertIn(" · 2 units · ➕ 0 · ✏️ 1 · 🗑️ 0 · ♻️ 0\n", self.comment)
        self.assertIn("**Needed:** review and merge; the apply on `main` runs by itself.",
                      self.comment)
        self.assertIn("**1 unit without a change:** `organization`", self.comment)

    def test_noise_left_out(self):
        head = self.comment.split("<details>")[0]
        for noise in ("etag", "html_url", "node_id", "known after apply"):
            self.assertNotIn(noise, head)


class RealAzure(unittest.TestCase):
    """iac-azure: a tag through rg.hcl, a Key Vault setting and list, a removed role assignment."""

    @classmethod
    def setUpClass(cls):
        base = HERE / "real" / "azure"
        cls.result = run((base / "plan.txt").read_text(encoding="utf-8"), "--github-output",
                         f"--json-dir={base / 'plan-json'}", "--trivy=none",
                         "--checks=formatting=success,validation=success")
        cls.comment = cls.result["comment"]
        cls.expected = json.loads((base / "expected.json").read_text(encoding="utf-8"))

    def test_destroys(self):
        self.assertEqual(table(self.comment, "### ⚠️ Destroys and replaces"),
                         self.expected["destroys"])

    def test_changes(self):
        self.assertEqual(table(self.comment, "### Changes"), self.expected["changes"])

    def test_verdict(self):
        self.assertEqual(self.result["github_output"], self.expected["github_output"])

    def test_fits(self):
        self.assertLessEqual(len(self.comment), MAX)


class OverLimit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = sample("over-limit.txt")
        cls.result = run(cls.text, "--github-output")
        cls.comment = cls.result["comment"]

    def test_fits(self):
        self.assertLessEqual(len(self.comment), MAX)

    def test_largest_unit_shortened_first(self):
        self.assertEqual(diff_states(self.comment),
                         {"big/a": "shortened", "big/b": "whole", "small/c": "whole"})

    def test_tables_and_errors_stay_whole(self):
        block = unit_lines(self.text, "shared/vault", "STDERR")
        self.assertIn("\n".join(block), self.comment)
        self.assertEqual(len(table(self.comment, "### Changes")), 40)

    def test_note_says_what_was_cut(self):
        self.assertTrue(self.comment.endswith(
            "_Cut to fit a comment: the details of `big/a` are shortened. The full plan is in "
            "artifact `plan-4242` (plan.txt)._"))

    def test_artifact_can_be_named(self):
        comment = run(self.text, "--artifact=drift-4242")["comment"]
        self.assertTrue(comment.endswith("artifact `drift-4242` (plan.txt)._"))

    def test_next_unit_when_the_largest_is_not_enough(self):
        text = (self.text + rename(self.text, "big/a", "big/a2")
                + rename(self.text, "big/b", "big/b2"))
        comment = run(text)["comment"]
        self.assertLessEqual(len(comment), MAX)
        states = diff_states(comment)
        self.assertEqual(states["big/a"], "left out")
        self.assertEqual(states["big/a2"], "shortened")
        for unit in ["big/b", "big/b2", "small/c"]:
            self.assertEqual(states[unit], "whole", unit)
        self.assertEqual(len(table(comment, "### Changes")), 26 * 2 + 12 * 2 + 2)

    def test_details_cut_before_the_tables(self):
        text = self.text + "".join(rename(self.text, "big/a", f"copy/{i:02d}") for i in range(20))
        comment = run(text)["comment"]
        self.assertLessEqual(len(comment), MAX)
        self.assertEqual(len(table(comment, "### Changes")), 40 + 26 * 20)
        self.assertIn("left out", diff_states(comment).values())
        self.assertIn("_Cut to fit a comment: the details of ", comment)

    def test_rows_capped_only_when_nothing_else_is_left(self):
        text = self.text + "".join(rename(self.text, "big/a", f"copy/{i:03d}") for i in range(80))
        comment = run(text)["comment"]
        self.assertLessEqual(len(comment), MAX)
        self.assertRegex(comment, r"\| \| \| \| _\d+ more rows, see artifact `plan-4242`")
        self.assertIn(" · ➕ 2120 · ", comment)
        self.assertEqual(len(re.findall(r"^```", comment, re.M)) % 2, 0)


class Values(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(SRC))
        import plan_details
        cls.pd = plan_details

    def test_resource_id_shortened_to_its_name(self):
        self.assertEqual(self.pd.fmt("/subscriptions/0000/resourceGroups/rg-x/providers/"
                                     "Microsoft.KeyVault/vaults/kv-x"), "`kv-x`")

    def test_text_with_an_id_inside_kept_whole(self):
        query = ('// Logic App runs\nunion workspace("/subscriptions/0000/resourceGroups/rg-x/'
                 'providers/Microsoft.OperationalInsights/workspaces/law-x").AzureDiagnostics')
        self.assertEqual(self.pd.fmt(query), "`" + query.replace("\n", "⏎")[:59] + "…`")

    def test_kind_from_the_dictionary_or_the_type(self):
        self.assertEqual(self.pd.kind("azurerm_key_vault"), "Key Vault")
        self.assertEqual(self.pd.kind("github_repository"), "Repository")
        self.assertEqual(self.pd.kind("routeros_ip_firewall_filter"), "Firewall rule")
        self.assertEqual(self.pd.kind("azurerm_container_app_environment"), "Container app environment")
        self.assertEqual(self.pd.kind("azapi_resource", {"type": "Microsoft.Foo/barBazes@2024-01-01"}),
                         "Bar baze")

    def test_diagnostic_setting_created(self):
        rc = {"address": "azurerm_monitor_diagnostic_setting.this", "type":
              "azurerm_monitor_diagnostic_setting", "mode": "managed", "name": "this",
              "change": {"actions": ["create"], "before": None, "after": {
                  "name": "diag-law", "log_analytics_workspace_id":
                  "/subscriptions/0/resourceGroups/rg/providers/Microsoft.OperationalInsights/"
                  "workspaces/law-sec", "enabled_log": [{"category": "AuditEvent"},
                                                        {"category_group": "allLogs"}],
                  "enabled_metric": [{"category": "AllMetrics"}]}}}
        e = self.pd.entry(rc, "u")
        self.assertEqual(e["rows"], [("diagnostic settings added: logs `AuditEvent`, `allLogs`; "
                                      "metrics `AllMetrics` → workspace `law-sec`", "—", "—")])

    def test_tag_added_and_removed(self):
        rows = self.pd.attribute_rows({"before": {"tags": {"a": "1"}}, "after": {"tags": {"b": "2"}}})
        self.assertEqual(rows, [("tag `a`", "`1`", "—"), ("tag `b`", "—", "`2`")])

    def test_json_string_compared_by_its_content(self):
        rows = self.pd.attribute_rows({"before": {"policy": '{"a": 1, "b": 2}'},
                                       "after": {"policy": '{"b": 2, "a": 3}'}})
        self.assertEqual(rows, [("setting `policy.a`", "`1`", "`3`")])


class ApplyComment(unittest.TestCase):
    def apply(self, plan_args, apply_text, *args):
        with tempfile.TemporaryDirectory() as tmp:
            changes = pathlib.Path(tmp, "changes.json")
            base, json_dir = plan_args
            subprocess.run([sys.executable, str(SCRIPT), str(base), f"--json-dir={json_dir}",
                            f"--changes-out={changes}"], capture_output=True, check=True,
                           env={**os.environ, **ENV})
            return run(apply_text, f"--changes={changes}", *args, script=APPLY)["comment"]

    def test_done(self):
        base = HERE / "real" / "github"
        comment = self.apply((base / "plan.txt", base / "plan-json"),
                             (HERE / "apply" / "handbook.txt").read_text(),
                             "--outcome=success", "--started=1000", "--ended=1133")
        self.assertTrue(comment.startswith("## ✅ Apply complete\n\n`bearclave/iac-test` · merge "
                                           "[`0123456`](https://github.com/bearclave/iac-test/commit/"))
        self.assertIn(" · 1 unit · ⏱️ 2 min 13 s · [run](https://github.com/bearclave/iac-test/"
                      "actions/runs/4242)", comment)
        self.assertIn("| ✏️ | Repository | `handbook` | setting `description` changed: `Field notes "
                      "and gotchas from Azure security work.` → `Field notes and gotchas from Azure "
                      "security work (test).` | ✅ done |", comment)
        self.assertIn("### Verification\n\n_No post-apply check has reported._", comment)

    def test_failed(self):
        comment = self.apply((HERE / "readable.txt", HERE / "readable"),
                             (HERE / "apply" / "rg-web.txt").read_text(), "--outcome=failure")
        self.assertTrue(comment.startswith("## ❌ Apply failed"))
        rows = [line for line in comment.splitlines() if line.startswith("| ")][1:]
        by_kind = {}
        for r in rows:
            cells = [c.strip() for c in re.split(r"(?<!\\)\|", r)[1:-1]]
            by_kind.setdefault(cells[1], cells[4])
        self.assertEqual(by_kind["Role assignment"], "✅ done")
        self.assertEqual(by_kind["Resource group"], "✅ done")
        self.assertTrue(by_kind["Key Vault"].startswith("❌ failed: updating Key Vault"))
        self.assertEqual(by_kind["Static Web App"], "⏭️ not applied")
        self.assertIn("### ❌ Errors\n\n**`mg/rg-web`**\n\n```\nError: updating Key Vault", comment)
        self.assertIn("| ✏️ | Key Vault secret | `api-key` | setting `value` changed (sensitive) |",
                      comment)

    def test_verification_section(self):
        base = HERE / "real" / "github"
        with tempfile.TemporaryDirectory() as tmp:
            pathlib.Path(tmp, "a").mkdir()
            pathlib.Path(tmp, "a", "verification.md").write_text(
                "### Verification\n\n| Check | Result | Detail |\n|---|---|---|\n"
                "| description | ✅ | matches |\n")
            comment = self.apply((base / "plan.txt", base / "plan-json"),
                                 (HERE / "apply" / "handbook.txt").read_text(), f"--verification={tmp}")
        self.assertTrue(comment.endswith("### Verification\n\n| Check | Result | Detail |\n|---|---|---|\n"
                                         "| description | ✅ | matches |"))

    def test_unit_not_run(self):
        base = HERE / "real" / "github"
        comment = self.apply((base / "plan.txt", base / "plan-json"), "", "--outcome=cancelled")
        self.assertTrue(comment.startswith("## ❌ Apply failed"))
        self.assertIn("| ⏭️ not run |", comment)
        self.assertIn("The apply job ended as `cancelled`", comment)


if __name__ == "__main__":
    unittest.main(verbosity=2)
