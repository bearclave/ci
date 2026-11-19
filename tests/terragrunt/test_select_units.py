#!/usr/bin/env python3
"""Runs select-units.py against throwaway git repositories.

Each test builds a small tree of units in a temporary repository, commits a
base, makes a change and checks the selection. expand-dependencies.py asks
terragrunt for the dependency graph; a stand-in terragrunt on PATH answers
from DEPENDENCIES below, so the tests need neither terragrunt nor the tenant.

Run: python3 tests/terragrunt/test_select_units.py
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "src" / "terragrunt" / "select-units.py"

TREE = [
    "root.hcl",
    "account.hcl",
    "_common/deps/groups.hcl",
    "README.md",
    ".github/workflows/terragrunt.yml",
    "tenant/groups/terragrunt.hcl",
    "tenant/memberships/terragrunt.hcl",
    "mg-corp/terragrunt.hcl",
    "mg-corp/mg.hcl",
    "mg-corp/sub-a/subscription.hcl",
    "mg-corp/sub-a/terragrunt.hcl",
    "mg-corp/sub-a/weu/region.hcl",
    "mg-corp/sub-a/weu/rg-x/rg.hcl",
    "mg-corp/sub-a/weu/rg-x/terragrunt.hcl",
    "mg-corp/sub-a/weu/rg-x/key-vault/terragrunt.hcl",
    "mg-corp/sub-a/weu/rg-x/key-vault/certificate/terragrunt.hcl",
    "mg-corp/sub-a/weu/rg-x/sentinel/terragrunt.hcl",
    "mg-corp/sub-a/weu/rg-x/sentinel/rules/one.json",
    "mg-corp/sub-a/weu/rg-x/sentinel/rules/one.kql",
]

# unit -> the units it depends on
DEPENDENCIES = {
    "mg-corp/sub-a/weu/rg-x/key-vault": ["mg-corp/sub-a/weu/rg-x"],
    "mg-corp/sub-a/weu/rg-x/key-vault/certificate": ["mg-corp/sub-a/weu/rg-x/key-vault"],
    "tenant/memberships": ["tenant/groups"],
}

FAKE_TERRAGRUNT = """#!/usr/bin/env python3
import json, os, sys
units = json.loads(os.environ["FAKE_UNITS"])
deps = json.loads(os.environ["FAKE_DEPENDENCIES"])
print(json.dumps([{"type": "unit", "path": u, "dependencies": deps.get(u, [])}
                  for u in units]))
"""


class SelectUnits(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = pathlib.Path(self.tmp.name, "repo")
        self.bin = pathlib.Path(self.tmp.name, "bin")
        self.repo.mkdir()
        self.bin.mkdir()
        fake = self.bin / "terragrunt"
        fake.write_text(FAKE_TERRAGRUNT)
        fake.chmod(0o755)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "test@example.com")
        self.git("config", "user.name", "test")
        self.git("config", "commit.gpgsign", "false")
        for f in TREE:
            self.write(f, f"# {f}\n")
        self.base = self.commit("base")

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.repo, check=True,
                              capture_output=True, text=True).stdout.strip()

    def write(self, path, text="changed\n"):
        p = self.repo / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)

    def remove(self, path):
        self.git("rm", "-q", "-r", path)

    def commit(self, message="change"):
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", message)
        return self.git("rev-parse", "HEAD")

    def select(self, base=None, *flags):
        units = sorted(str(p.parent.relative_to(self.repo))
                       for p in self.repo.rglob("terragrunt.hcl") if ".git" not in p.parts)
        env = dict(os.environ,
                   PATH=f"{self.bin}{os.pathsep}{os.environ['PATH']}",
                   FAKE_UNITS=json.dumps(units),
                   FAKE_DEPENDENCIES=json.dumps(DEPENDENCIES))
        out = subprocess.run([sys.executable, str(SCRIPT), self.base if base is None else base,
                              *flags], cwd=self.repo, env=env, check=True,
                             capture_output=True, text=True).stdout
        return json.loads(out)

    def test_unit_file_selects_that_unit(self):
        self.write("tenant/groups/terragrunt.hcl")
        self.commit()
        r = self.select()
        self.assertFalse(r["all"])
        self.assertEqual(r["units"], ["tenant/groups"])
        self.assertEqual(r["base"], self.base)

    def test_file_inside_a_unit_selects_the_nearest_unit(self):
        self.write("mg-corp/sub-a/weu/rg-x/sentinel/rules/one.kql")
        self.commit()
        self.assertEqual(self.select()["units"], ["mg-corp/sub-a/weu/rg-x/sentinel"])

    def test_dependencies_are_pulled_in(self):
        self.write("mg-corp/sub-a/weu/rg-x/key-vault/certificate/terragrunt.hcl")
        self.commit()
        self.assertEqual(self.select()["units"], [
            "mg-corp/sub-a/weu/rg-x",
            "mg-corp/sub-a/weu/rg-x/key-vault",
            "mg-corp/sub-a/weu/rg-x/key-vault/certificate",
        ])

    def test_no_expand_leaves_dependencies_out(self):
        self.write("tenant/memberships/terragrunt.hcl")
        self.commit()
        self.assertEqual(self.select(None, "--no-expand")["units"], ["tenant/memberships"])

    def test_context_file_selects_every_unit_below(self):
        self.write("mg-corp/sub-a/weu/region.hcl")
        self.commit()
        self.assertEqual(self.select(None, "--no-expand")["units"], [
            "mg-corp/sub-a/weu/rg-x",
            "mg-corp/sub-a/weu/rg-x/key-vault",
            "mg-corp/sub-a/weu/rg-x/key-vault/certificate",
            "mg-corp/sub-a/weu/rg-x/sentinel",
        ])

    def test_context_file_selects_the_unit_beside_it(self):
        # rg.hcl sits next to the resource group's own terragrunt.hcl.
        self.write("mg-corp/sub-a/weu/rg-x/rg.hcl")
        self.commit()
        self.assertEqual(self.select(None, "--no-expand")["units"], [
            "mg-corp/sub-a/weu/rg-x",
            "mg-corp/sub-a/weu/rg-x/key-vault",
            "mg-corp/sub-a/weu/rg-x/key-vault/certificate",
            "mg-corp/sub-a/weu/rg-x/sentinel",
        ])

    def test_root_account_and_common_select_everything(self):
        for path in ("root.hcl", "account.hcl", "_common/deps/groups.hcl"):
            with self.subTest(path=path):
                self.write(path, f"changed {path}\n")
                self.commit()
                r = self.select()
                self.assertTrue(r["all"])
                self.assertEqual(r["units"], [])

    def test_workflow_alone_selects_everything(self):
        self.write(".github/workflows/terragrunt.yml")
        self.commit()
        self.assertTrue(self.select()["all"])

    def test_workflow_with_a_unit_selects_only_the_unit(self):
        self.write(".github/workflows/terragrunt.yml")
        self.write("tenant/groups/terragrunt.hcl")
        self.commit()
        r = self.select()
        self.assertFalse(r["all"])
        self.assertEqual(r["units"], ["tenant/groups"])

    def test_docs_alone_select_nothing(self):
        # A push of a README, an agent rule or a GitHub config plans nothing.
        self.write("README.md")
        self.write(".claude/rules/x.md")
        self.write(".github/conventions.yaml")
        self.write(".github/CODEOWNERS")
        self.commit()
        r = self.select()
        self.assertFalse(r["all"])
        self.assertEqual(r["units"], [])

    def test_plan_inputs_under_github_select_everything(self):
        for path in (".github/tool-versions.env", ".github/trivyignore.yaml"):
            with self.subTest(path=path):
                self.write(path, f"{path} changed\n")
                self.commit()
                self.assertTrue(self.select(self.git("rev-parse", "HEAD~1"))["all"])

    def test_scripts_outside_units_select_everything(self):
        self.write("tools/home-vpn.sh")
        self.commit()
        self.assertTrue(self.select()["all"])

    def test_deleted_unit_is_a_destroy_not_its_parent(self):
        self.remove("mg-corp/sub-a/weu/rg-x/sentinel")
        self.commit()
        r = self.select()
        self.assertFalse(r["all"])
        self.assertEqual(r["units"], [])
        self.assertEqual(r["deleted"], ["mg-corp/sub-a/weu/rg-x/sentinel"])

    def test_deleted_unit_with_a_changed_one(self):
        self.remove("mg-corp/sub-a/weu/rg-x/key-vault/certificate")
        self.write("tenant/groups/terragrunt.hcl")
        self.commit()
        r = self.select()
        self.assertEqual(r["units"], ["tenant/groups"])
        self.assertEqual(r["deleted"], ["mg-corp/sub-a/weu/rg-x/key-vault/certificate"])

    def test_deleted_unit_survives_a_whole_tree_run(self):
        self.remove("tenant/memberships")
        self.write("root.hcl", "changed\n")
        self.commit()
        r = self.select()
        self.assertTrue(r["all"])
        self.assertEqual(r["deleted"], ["tenant/memberships"])

    def test_deleted_unit_with_a_workflow_change(self):
        self.remove("tenant/memberships")
        self.write(".github/workflows/terragrunt.yml")
        self.commit()
        r = self.select()
        self.assertTrue(r["all"])
        self.assertEqual(r["deleted"], ["tenant/memberships"])

    def test_renamed_unit_is_refused(self):
        self.git("mv", "tenant/groups", "tenant/entra-groups")
        self.commit()
        r = self.select()
        self.assertEqual(r["renamed"], [["tenant/groups", "tenant/entra-groups"]])
        self.assertEqual(r["deleted"], [])

    def test_changes_since_an_older_base_add_up(self):
        # The base is the last applied commit; a failed apply in between must
        # not lose the units it was about.
        self.write("tenant/groups/terragrunt.hcl")
        self.commit("not applied")
        self.write("tenant/memberships/terragrunt.hcl")
        self.commit("next")
        self.assertEqual(self.select(None, "--no-expand")["units"],
                         ["tenant/groups", "tenant/memberships"])

    def test_no_difference_selects_everything(self):
        self.assertTrue(self.select(self.git("rev-parse", "HEAD"))["all"])

    def test_unknown_base_selects_everything(self):
        self.write("tenant/groups/terragrunt.hcl")
        self.commit()
        for base in ("", "0" * 40, "nonsense"):
            with self.subTest(base=base):
                r = self.select(base)
                self.assertTrue(r["all"])
                self.assertIsNone(r["base"])

    def test_base_that_is_not_an_ancestor_selects_everything(self):
        self.git("checkout", "-q", "-b", "elsewhere", self.base)
        self.write("tenant/groups/terragrunt.hcl", "elsewhere\n")
        other = self.commit("elsewhere")
        self.git("checkout", "-q", "main")
        self.write("tenant/memberships/terragrunt.hcl")
        self.commit()
        r = self.select(other)
        self.assertTrue(r["all"])
        self.assertIsNone(r["base"])


if __name__ == "__main__":
    unittest.main()
