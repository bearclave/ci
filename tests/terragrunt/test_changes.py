#!/usr/bin/env python3
"""Tests of changes.py: which pull requests of a Terragrunt repository plan.

Run: python3 tests/terragrunt/test_changes.py
"""
import importlib.util
import pathlib
import subprocess
import sys
import tempfile
import unittest

SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "src" / "terragrunt" / "changes.py"
spec = importlib.util.spec_from_file_location("changes", SCRIPT)
changes = importlib.util.module_from_spec(spec)
spec.loader.exec_module(changes)

# The default relevant-paths of terragrunt.yml.
RELEVANT = r"^(mg-corp/|tenant/|_common/|\.github/)|^(root|account)\.hcl$"
UNITS = ["tenant/groups/terragrunt.hcl", "mg-corp/sub-a/weu/rg-x/kv/terragrunt.hcl"]


def relevant(*files, units=UNITS):
    return changes.relevant(RELEVANT, list(files), units)


class Relevant(unittest.TestCase):
    def test_unit_and_globals_plan(self):
        self.assertTrue(relevant("tenant/groups/terragrunt.hcl"))
        self.assertTrue(relevant("root.hcl"))
        self.assertTrue(relevant("_common/templates/versions.tf.tmpl"))
        self.assertTrue(relevant(".github/workflows/ci.yml"))
        self.assertTrue(relevant(".github/tool-versions.env"))

    def test_github_configs_do_not_plan(self):
        self.assertFalse(relevant(".github/dependabot.yml"))
        self.assertFalse(relevant(".github/release.yml"))
        self.assertFalse(relevant(".github/dependabot.yml", ".github/release.yml"))
        self.assertFalse(relevant(".github/conventions.yaml", ".github/sentinel-lint.yaml",
                                  ".github/CODEOWNERS", ".github/markdownlint.yaml",
                                  ".github/PULL_REQUEST_TEMPLATE.md", ".github/ISSUE_TEMPLATE/bug.yml"))

    def test_github_plan_inputs_plan(self):
        self.assertTrue(relevant(".github/trivyignore.yaml"))
        self.assertTrue(relevant(".github/workflows/drift.yml"))

    def test_claude_does_not_plan(self):
        self.assertFalse(relevant(".claude/rules/security.md", ".claude/settings.json"))

    def test_markdown_outside_units_does_not_plan(self):
        self.assertFalse(relevant("README.md", "_common/README.md", ".github/README.md", "docs/x.md"))
        self.assertFalse(relevant("tenant/README.md"))

    def test_markdown_inside_a_unit_plans(self):
        self.assertTrue(relevant("tenant/groups/README.md"))
        self.assertTrue(relevant("mg-corp/sub-a/weu/rg-x/kv/docs/notes.MD"))

    def test_markdown_counts_when_units_are_unknown(self):
        self.assertTrue(relevant("_common/README.md", units=None))
        self.assertTrue(relevant("_common/README.md", units=[]))

    def test_one_relevant_file_is_enough(self):
        self.assertTrue(relevant(".github/dependabot.yml", "README.md", "tenant/groups/main.tf"))

    def test_outside_the_regex_does_not_plan(self):
        self.assertFalse(relevant("tools/x.sh", "docs/a.png"))
        self.assertFalse(relevant())

    def test_similar_names_still_plan(self):
        self.assertTrue(relevant(".github/workflows/release.yml"))
        self.assertTrue(relevant("_common/claude/x.hcl"))


class Cli(unittest.TestCase):
    def run_cli(self, files, units=None):
        with tempfile.TemporaryDirectory() as tmp:
            f = pathlib.Path(tmp, "files")
            f.write_text("\n".join(files) + "\n")
            args = [sys.executable, str(SCRIPT), RELEVANT, str(f)]
            if units is not None:
                u = pathlib.Path(tmp, "units")
                u.write_text("\n".join(units))
                args.append(str(u))
            return subprocess.run(args, capture_output=True, text=True, check=True).stdout.strip()

    def test_output(self):
        self.assertEqual(self.run_cli([".github/dependabot.yml", "README.md"], UNITS), "relevant=false")
        self.assertEqual(self.run_cli(["tenant/groups/README.md"], UNITS), "relevant=true")
        self.assertEqual(self.run_cli(["_common/README.md"], []), "relevant=true")
        self.assertEqual(self.run_cli(["_common/README.md"]), "relevant=true")

    def test_missing_files_list_plans_nothing(self):
        out = subprocess.run([sys.executable, str(SCRIPT), RELEVANT, "/nonexistent"],
                             capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(out, "relevant=false")


if __name__ == "__main__":
    unittest.main()
