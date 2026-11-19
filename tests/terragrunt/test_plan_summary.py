#!/usr/bin/env python3
"""Runs plan-summary.py over sample terragrunt output and checks the comment.

The samples are real `terragrunt run --all plan --no-color` output over units
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
                  JSON, written by hand after terraform's format (azurerm and
                  azuread resources need a tenant), for the table per resource

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
SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "src" / "terragrunt" / "plan-summary.py"
MAX = 60_000
RE_UNIT = re.compile(r"\[([^\]]+)\] terraform: ?")


def run(text: str, *args: str) -> dict:
    """Runs the script over the given output the way the workflows do."""
    with tempfile.TemporaryDirectory() as tmp:
        plan = pathlib.Path(tmp, "plan.txt")
        plan.write_text(text, encoding="utf-8")
        github_output = pathlib.Path(tmp, "github_output")
        github_output.touch()
        env = {**os.environ, "GITHUB_RUN_ID": "4242", "GITHUB_OUTPUT": str(github_output)}
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), str(plan), *args],
            capture_output=True, text=True, env=env, check=False,
        )
        return {
            "comment": proc.stdout.rstrip("\n"),
            "code": proc.returncode,
            "stderr": proc.stderr,
            "github_output": github_output.read_text(encoding="utf-8"),
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
    """For each unit with a change: whole, shortened or left out."""
    states = {}
    for part in comment.split("\n#### ")[1:]:
        unit = part.split("`")[1]
        if "Attribute diff left out" in part:
            states[unit] = "left out"
        elif "Attribute diff, shortened" in part:
            states[unit] = "shortened"
        elif "<details>" in part:
            states[unit] = "whole"
    return states


class MultiUnit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = sample("multi-unit.txt")
        cls.result = run(cls.text, "--github-output")
        cls.comment = cls.result["comment"]

    def test_sections_come_in_order(self):
        heads = ["## Terragrunt plan", "### Errors", "### Destroys or replaces",
                 "### Units with a change", "**1 unit without a change:**"]
        positions = [self.comment.index(h) for h in heads]
        self.assertEqual(positions, sorted(positions))

    def test_totals(self):
        self.assertIn("**4 to add, 2 to change, 4 to destroy, 1 to move, 1 to forget, "
                      "outputs change in 1 unit** across 4 units.", self.comment)

    def test_error_block_is_whole(self):
        block = unit_lines(self.text, "shared/vault", "STDERR")
        self.assertTrue(block[0].startswith("Error: "))
        self.assertGreater(len(block), 5)
        self.assertIn("#### `shared/vault`\n\n```\n" + "\n".join(block) + "\n```", self.comment)

    def test_destroys_and_replaces(self):
        for line in [
            "- `web/site: terraform_data.cert must be replaced`",
            "- `web/site: terraform_data.old will be destroyed`",
            "- `web/misc: terraform_data.a is tainted, so must be replaced`",
            '- `web/misc: terraform_data.k["Reader on sub"] will be destroyed`',
        ]:
            self.assertIn(line, self.comment)

    def test_resources_with_their_action(self):
        expected = {
            "web/site": [
                "- `data.terraform_remote_state.peer`: read",
                "- `terraform_data.cdn`: create",
                "- `terraform_data.cert`: replace",
                "- `terraform_data.old`: destroy",
                "- `terraform_data.site`: update in-place",
                "- output values change",
            ],
            "web/misc": [
                "- `terraform_data.a`: replace (tainted)",
                "- `terraform_data.d`: forget",
                '- `terraform_data.k["Reader on sub"]`: destroy',
                "- `terraform_data.m`: move to `terraform_data.m2`",
            ],
            "web/db": [
                "- `terraform_data.db`: update in-place",
                "- `terraform_data.replica`: create",
            ],
        }
        for unit, items in expected.items():
            head = self.comment.index(f"#### `{unit}` (")
            listed = self.comment[head:].split("\n\n")[1].split("\n")
            self.assertEqual(listed, items, unit)

    def test_diff_is_what_terraform_printed(self):
        for unit in ["web/site", "web/misc", "web/db"]:
            lines = unit_lines(self.text, unit)
            expected = section(lines, "Terraform will perform the following actions:", "Plan: ")
            if "Changes to Outputs:" in lines:
                expected += ["Changes to Outputs:"] + [
                    x for x in lines[lines.index("Changes to Outputs:") + 1:] if x[:1].isspace()]
            shown = [as_terraform(x) for x in diff_of(self.comment, unit) if x]
            self.assertEqual(shown, expected, unit)

    def test_diff_is_folded_and_coloured(self):
        self.assertEqual(self.comment.count("<details><summary>Attribute diff</summary>"), 3)
        diff = diff_of(self.comment, "web/db")
        self.assertIn("!           size = 10 -> 20", diff)
        self.assertIn('+           zone = "2"', diff)
        self.assertIn("-           size = 10", diff)

    def test_sensitive_values_stay_masked(self):
        masked = "(sensitive value)"
        self.assertEqual(self.comment.count(masked), self.text.count(masked))
        self.assertIn("+           token = (sensitive value)", diff_of(self.comment, "web/site"))

    def test_prefix_is_gone(self):
        self.assertNotRegex(self.comment, r"\] terraform:")
        self.assertNotRegex(self.comment, r"\d\d:\d\d:\d\d\.\d{3}")

    def test_unit_without_a_change_is_one_line(self):
        self.assertTrue(self.comment.endswith("\n**1 unit without a change:** `shared/dns`"))

    def test_nothing_shortened_or_missing(self):
        self.assertNotIn("Shortened", self.comment)
        self.assertNotIn("Without a result", self.comment)

    def test_verdict(self):
        self.assertEqual(self.result["github_output"], "has_changes=true\nhas_errors=true\nhas_destroys=true\n")
        drift = run(self.text, "--fail-on-change")
        self.assertEqual(drift["code"], 1)
        self.assertIn("DRIFT: 4 to add, 2 to change, 4 to destroy, 1 to move, 1 to forget, "
                      "outputs change in 1 unit, 3 errors.", drift["stderr"])
        self.assertEqual(drift["comment"], self.comment)

    def test_interleaving_does_not_matter(self):
        self.assertEqual(run(interleave(self.text))["comment"], self.comment)


class NoChange(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = run(sample("no-change.txt"), "--github-output", "--fail-on-change")
        cls.comment = cls.result["comment"]

    def test_comment(self):
        self.assertEqual(self.comment, "\n".join([
            "## Terragrunt plan",
            "",
            "**0 to add, 0 to change, 0 to destroy** across 3 units.",
            "",
            "**3 units without a change:** `tenant/groups`, `tenant/users`, `web/site`",
        ]))

    def test_verdict(self):
        self.assertEqual(self.result["code"], 0)
        self.assertEqual(self.result["github_output"], "has_changes=false\nhas_errors=false\nhas_destroys=false\n")


class Import(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = sample("import.txt")
        cls.result = run(cls.text, "--github-output")
        cls.comment = cls.result["comment"]

    def test_totals(self):
        self.assertIn("**2 to import, 0 to add, 1 to change, 0 to destroy** across 3 units.",
                      self.comment)

    def test_resources_with_their_action(self):
        self.assertIn("#### `tenant/users` (1 to import, 0 to add, 0 to change, 0 to destroy)"
                      "\n\n- `terraform_data.guest`: import\n", self.comment)
        self.assertIn("\n- `terraform_data.team`: update in-place (imported)\n", self.comment)

    def test_import_alone_is_a_change(self):
        alone = only(self.text, "tenant/users", "shared/dns")
        result = run(alone, "--github-output")
        self.assertIn("**1 to import, 0 to add, 0 to change, 0 to destroy** across 2 units.",
                      result["comment"])
        self.assertEqual(result["github_output"], "has_changes=true\nhas_errors=false\nhas_destroys=false\n")
        drift = run(alone, "--fail-on-change")
        self.assertEqual(drift["code"], 1)
        self.assertIn("DRIFT: 1 to import, 0 to add, 0 to change, 0 to destroy, 0 errors.",
                      drift["stderr"])


class MoveForget(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = sample("move-forget.txt")
        cls.result = run(cls.text, "--github-output")
        cls.comment = cls.result["comment"]

    def test_totals(self):
        self.assertIn("**0 to add, 0 to change, 0 to destroy, 1 to move, 1 to forget** "
                      "across 3 units.", self.comment)

    def test_resources_with_their_action(self):
        self.assertIn("\n- `terraform_data.team`: move to `terraform_data.sales`\n", self.comment)
        self.assertIn("\n- `terraform_data.legacy`: forget\n", self.comment)

    def test_verdict(self):
        self.assertEqual(self.result["github_output"], "has_changes=true\nhas_errors=false\nhas_destroys=false\n")
        drift = run(self.text, "--fail-on-change")
        self.assertEqual(drift["code"], 1)
        self.assertIn("DRIFT: 0 to add, 0 to change, 0 to destroy, 1 to move, 1 to forget, "
                      "0 errors.", drift["stderr"])

    def test_each_alone_is_a_change(self):
        for unit, totals in [("tenant/groups", "1 to move"), ("tenant/users", "1 to forget")]:
            result = run(only(self.text, unit, "shared/dns"), "--github-output")
            self.assertIn(f"**0 to add, 0 to change, 0 to destroy, {totals}** across 2 units.",
                          result["comment"])
            self.assertEqual(result["github_output"], "has_changes=true\nhas_errors=false\nhas_destroys=false\n",
                             unit)


class OutputsOnly(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = sample("outputs-only.txt")
        cls.result = run(cls.text, "--github-output")
        cls.comment = cls.result["comment"]

    def test_comment(self):
        self.assertIn("**0 to add, 0 to change, 0 to destroy, outputs change in 1 unit** "
                      "across 2 units.", self.comment)
        self.assertIn("#### `tenant/groups`\n\n- output values change\n", self.comment)
        self.assertIn("Changes to Outputs:\n+   team_id = ", self.comment)
        self.assertTrue(self.comment.endswith("**1 unit without a change:** `shared/dns`"))

    def test_verdict(self):
        self.assertEqual(self.result["github_output"], "has_changes=true\nhas_errors=false\nhas_destroys=false\n")
        drift = run(self.text, "--fail-on-change")
        self.assertEqual(drift["code"], 1)
        self.assertIn("DRIFT: 0 to add, 0 to change, 0 to destroy, outputs change in 1 unit, "
                      "0 errors.", drift["stderr"])


class Cancelled(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = sample("cancelled.txt")
        cls.result = run(cls.text, "--github-output")
        cls.comment = cls.result["comment"]

    def test_unit_without_a_result_is_an_error(self):
        self.assertIn("**3 to add, 0 to change, 0 to destroy** across 2 units.", self.comment)
        self.assertIn("### Errors\n\n#### Without a result\n\n`tenant/groups`\n", self.comment)
        self.assertIn("The rest is in artifact `plan-4242` (plan.txt).", self.comment)
        self.assertNotIn("`tenant/groups` (", self.comment)

    def test_verdict(self):
        self.assertEqual(self.result["github_output"], "has_changes=true\nhas_errors=true\nhas_destroys=false\n")
        drift = run(self.text, "--fail-on-change")
        self.assertEqual(drift["code"], 1)
        self.assertIn(", 0 errors, 1 unit without a result.", drift["stderr"])

    def test_no_change_but_cut_off_is_still_red(self):
        text = only(self.text, "tenant/groups") + "".join(
            line + "\n" for line in self.text.splitlines() if not RE_UNIT.search(line))
        drift = run(text, "--fail-on-change")
        self.assertIn("across 0 units", drift["comment"])
        self.assertEqual(drift["code"], 1)


class Destroy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = run(sample("destroy.txt"), "--github-output")
        cls.comment = cls.result["comment"]

    def test_totals(self):
        self.assertIn("**0 to add, 0 to change, 2 to destroy** across 2 units.", self.comment)

    def test_empty_state_is_not_an_error(self):
        self.assertNotIn("Without a result", self.comment)
        self.assertIn("**1 unit without a change:** `tenant/old-empty`", self.comment)

    def test_destroys_are_listed_with_the_manual_run(self):
        self.assertIn("### Destroys or replaces", self.comment)
        self.assertIn("waits for a manual run of the workflow with `allow_destroy`", self.comment)
        self.assertIn('- `tenant/memberships: terraform_data.m["a"] will be destroyed`', self.comment)

    def test_verdict(self):
        self.assertEqual(self.result["github_output"],
                         "has_changes=true\nhas_errors=false\nhas_destroys=true\n")


class Readable(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = run(sample("readable.txt"), f"--json-dir={HERE / 'readable'}")
        cls.comment = cls.result["comment"]

    def row(self, start):
        rows = [line for line in self.comment.splitlines() if line.startswith(start)]
        self.assertEqual(len(rows), 1, f"one row starting with {start!r}: {rows}")
        return rows[0]

    def test_table_replaces_the_address_list(self):
        self.assertIn("| | Resource | What changes |", self.comment)
        self.assertNotIn("- `azurerm_resource_group.this`: update in-place", self.comment)

    def test_destroys_first(self):
        verbs = [line.split("|")[1].strip() for line in self.comment.splitlines()
                 if line.startswith("| ") and "<sub>" in line]
        self.assertEqual(verbs, ["destroy", "replace", "update", "update", "update", "update",
                                 "update", "update", "create", "import", "move"])

    def test_role_assignment_by_role_principal_and_scope(self):
        row = self.row("| destroy | role assignment")
        self.assertIn("`Key Vault Secrets User` for `sp:sp-invoices` on `kv-app-fin`", row)
        self.assertIn("its key is no longer in the for_each", row)

    def test_tag_change(self):
        self.assertIn("tag `criticality`: `medium` → `high`", self.row("| update | resource group"))

    def test_replace_says_why(self):
        row = self.row("| replace | Key Vault certificate `cert-web`")
        self.assertIn("because `certificate_policy[0].x509_certificate_properties[0].subject` changes", row)
        self.assertIn("`CN=old.example.cz` → `CN=new.example.cz`", row)

    def test_boolean_change(self):
        self.assertIn("`public_network_access_enabled`: `true` → `false`",
                      self.row("| update | Key Vault `kv-sec`"))

    def test_sensitive_value_never_shown(self):
        self.assertIn("`value` changes (sensitive)", self.row("| update | Key Vault secret"))
        self.assertNotIn("s3cr3t", self.comment)

    def test_list_change_as_added_and_removed(self):
        self.assertIn("`tactics`: +`Persistence`", self.row("| update | analytic rule `0mega"))

    def test_unknown_after_apply(self):
        self.assertIn("`etag`: `abc` → (known after apply)", self.row("| update | Static Web App"))

    def test_azapi_by_its_type_and_display_name(self):
        row = self.row("| update | analytic rule `Ransomware group created`")
        self.assertNotIn("`output`", row)

    def test_long_strings_cut_to_the_difference(self):
        row = self.row("| update | analytic rule `Ransomware group created`")
        self.assertIn("…as been detected. {{{ResultMessage}} by user` → `…as been detected. {{ResultMessage}} by user`", row)

    def test_import_and_move(self):
        self.row("| import | Entra group `sg-import`")
        self.assertIn("moved from `azurerm_monitor_diagnostic_setting.this`",
                      self.row("| move | diagnostic setting"))

    def test_data_sources_left_out(self):
        self.assertNotIn("client config", self.comment)

    def test_outputs(self):
        self.assertIn("Outputs that change: `id`", self.comment)
        self.assertNotIn("`same`", self.comment)

    def test_raw_diff_still_there(self):
        self.assertIn("<details><summary>Attribute diff</summary>", self.comment)

    def test_without_json_the_address_list_stays(self):
        plain = run(sample("readable.txt"))["comment"]
        self.assertIn("- `azurerm_resource_group.this`: update in-place", plain)
        self.assertNotIn("| | Resource | What changes |", plain)

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
        self.assertIn("### Security findings", comment)
        self.assertIn("| `mg/rg-web` | HIGH | AVD-AZU-0013: Key vault network ACL default action "
                      "| `azurerm_key_vault.this` |", comment)
        self.assertNotIn("AVD-AZU-0016", comment)

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
            merged = run(sample("readable.txt"), f"--import={parts}")
            exported = json.loads((parts / "shard-0.json").read_text())
        self.assertEqual(merged["comment"], whole["comment"])
        self.assertEqual(merged["github_output"], whole["github_output"])
        # Only what the comment shows leaves a shard, never the plan JSON.
        self.assertEqual(set(exported), {"tables", "findings"})


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

    def test_summary_errors_and_resources_stay_whole(self):
        self.assertIn("**40 to add, 0 to change, 0 to destroy** across 4 units.", self.comment)
        block = unit_lines(self.text, "shared/vault", "STDERR")
        self.assertIn("\n".join(block), self.comment)
        for name, count in [("rule", 26), ("route", 12), ("probe", 2)]:
            for i in range(count):
                self.assertIn(f"- `terraform_data.{name}[{i}]`: create", self.comment)

    def test_note_at_the_end(self):
        self.assertTrue(self.comment.endswith("_Shortened to fit a comment; the full output "
                                              "is in artifact `plan-4242` (plan.txt)._"))

    def test_artifact_can_be_named(self):
        comment = run(self.text, "--artifact=drift-4242")["comment"]
        self.assertTrue(comment.endswith("artifact `drift-4242` (plan.txt)._"))

    def test_verdict_unchanged(self):
        self.assertEqual(self.result["github_output"], "has_changes=true\nhas_errors=true\nhas_destroys=false\n")

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
        self.assertEqual(comment.count("`: create"), 26 * 2 + 12 * 2 + 2)

    def test_cut_off_when_even_the_lists_do_not_fit(self):
        text = self.text + "".join(rename(self.text, "big/a", f"copy/{i:02d}") for i in range(60))
        comment = run(text)["comment"]
        self.assertLessEqual(len(comment), MAX)
        self.assertIn("**1600 to add, 0 to change, 0 to destroy** across 64 units.", comment)
        self.assertTrue(comment.endswith("_Cut off to fit a comment; the full output is in "
                                         "artifact `plan-4242` (plan.txt)._"))
        self.assertEqual(len(re.findall(r"^```", comment, re.M)) % 2, 0)


class Values(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(SCRIPT.parent))
        import plan_details
        cls.fmt = staticmethod(plan_details.fmt)

    def test_resource_id_shortened_to_its_name(self):
        self.assertEqual(self.fmt("/subscriptions/0000/resourceGroups/rg-x/providers/"
                                  "Microsoft.KeyVault/vaults/kv-x"), "`kv-x`")

    def test_text_with_an_id_inside_kept_whole(self):
        query = ('// Logic App runs\nunion workspace("/subscriptions/0000/resourceGroups/rg-x/'
                 'providers/Microsoft.OperationalInsights/workspaces/law-x").AzureDiagnostics')
        self.assertEqual(self.fmt(query), "`" + query.replace("\n", "⏎")[:59] + "…`")


if __name__ == "__main__":
    unittest.main(verbosity=2)
