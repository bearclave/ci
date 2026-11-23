#!/usr/bin/env python3
"""Runs select-units.py against throwaway git repositories.

Each test builds a small tree of units in a temporary repository, commits a
base, makes a change and checks the selection. select-units.py asks
terragrunt for the dependencies and for the files each unit includes and
reads; a stand-in terragrunt on PATH answers from DEPENDENCIES, READING and
INCLUDES below, so the tests need neither terragrunt nor the tenant.

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
    "_common/units/key-vault.hcl",
    "_common/templates/providers.tf.tmpl",
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
    "mg-corp/sub-b/subscription.hcl",
    "mg-corp/sub-b/terragrunt.hcl",
    "mg-corp/sub-b/weu/rg-y/terragrunt.hcl",
    "mg-corp/sub-b/weu/rg-y/key-vault/terragrunt.hcl",
]

# unit -> the units it depends on
DEPENDENCIES = {
    "mg-corp/sub-a/weu/rg-x/key-vault": ["mg-corp/sub-a/weu/rg-x"],
    "mg-corp/sub-a/weu/rg-x/key-vault/certificate": ["mg-corp/sub-a/weu/rg-x/key-vault"],
    "tenant/memberships": ["tenant/groups"],
}

# unit -> the files it reads (read_terragrunt_config) besides root.hcl
READING = {
    "mg-corp/sub-b/weu/rg-y/key-vault": ["mg-corp/sub-a/subscription.hcl"],
}

# unit -> its includes
INCLUDES = {
    "mg-corp/sub-a/weu/rg-x/key-vault": {"unit": "_common/units/key-vault.hcl"},
    "mg-corp/sub-b/weu/rg-y/key-vault": {"unit": "_common/units/key-vault.hcl"},
    "tenant/memberships": {"groups": "_common/deps/groups.hcl"},
}

FAKE_TERRAGRUNT = """#!/usr/bin/env python3
import json, os, sys
units = json.loads(os.environ["FAKE_UNITS"])
deps = json.loads(os.environ["FAKE_DEPENDENCIES"])
reading = json.loads(os.environ["FAKE_READING"])
includes = json.loads(os.environ["FAKE_INCLUDES"])
excluded = json.loads(os.environ.get("FAKE_EXCLUDED", "[]"))
print(json.dumps([{"type": "unit", "path": u, "dependencies": deps.get(u, []),
                   "exclude": {"if": u in excluded, "actions": ["all"]},
                   "include": {"root": "root.hcl", **includes.get(u, {})},
                   "reading": ["account.hcl", "root.hcl", *reading.get(u, [])]}
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

    def invoke(self, base=None, *flags, moves=None, dependencies=None, includes=None, excluded=()):
        units = sorted(str(p.parent.relative_to(self.repo))
                       for p in self.repo.rglob("terragrunt.hcl") if ".git" not in p.parts)
        env = dict(os.environ,
                   PATH=f"{self.bin}{os.pathsep}{os.environ['PATH']}",
                   FAKE_UNITS=json.dumps(units),
                   FAKE_DEPENDENCIES=json.dumps(DEPENDENCIES if dependencies is None else dependencies),
                   FAKE_READING=json.dumps(READING),
                   FAKE_INCLUDES=json.dumps(INCLUDES if includes is None else includes),
                   FAKE_EXCLUDED=json.dumps(list(excluded)))
        if moves is not None:
            path = pathlib.Path(self.tmp.name, "state-moves.json")
            path.write_text(json.dumps(moves))
            flags = (*flags, f"--moves={path}")
        return subprocess.run([sys.executable, str(SCRIPT), self.base if base is None else base,
                               *flags], cwd=self.repo, env=env, capture_output=True, text=True)

    def select(self, base=None, *flags, **kwargs):
        out = self.invoke(base, *flags, **kwargs)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

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

    def sentinel_workspace(self):
        """A workspace folder of iac-sentinel: its own unit and collection units below."""
        ws = "infrastructure/law-security"
        for f in ("workspace.hcl", "terragrunt.hcl", "analytic-rules/terragrunt.hcl",
                  "analytic-rules/entra-id/break-glass-sign-in.yaml", "watchlists/terragrunt.hcl",
                  "watchlists/break-glass-accounts.yaml", "logic-apps-standard/la-soar/terragrunt.hcl",
                  "logic-apps-standard/la-soar/workflows/revoke/workflow.json",
                  "logic-apps-consumption/pb-revoke/terragrunt.hcl",
                  "logic-apps-consumption/pb-revoke/definition.json"):
            self.write(f"{ws}/{f}", f"# {f}\n")
        self.base = self.commit("workspace")
        return ws

    def test_imports_and_moves_files_select_their_unit(self):
        # imports.hcl and moves.hcl are temporary files next to a unit: added
        # in the pull request that needs them, deleted in the next one.
        self.write("tenant/groups/imports.hcl", "locals {}\n")
        self.write("tenant/memberships/moves.hcl", "locals {}\n")
        self.commit()
        self.assertEqual(self.select(None, "--no-expand")["units"], ["tenant/groups", "tenant/memberships"])
        self.base = self.git("rev-parse", "HEAD")
        self.remove("tenant/groups/imports.hcl")
        self.remove("tenant/memberships/moves.hcl")
        self.commit()
        self.assertEqual(self.select(None, "--no-expand")["units"], ["tenant/groups", "tenant/memberships"])

    def test_collection_object_selects_its_collection(self):
        ws = self.sentinel_workspace()
        self.write(f"{ws}/analytic-rules/entra-id/break-glass-sign-in.yaml", "severity: High\n")
        self.commit()
        self.assertEqual(self.select(None, "--no-expand")["units"], [f"{ws}/analytic-rules"])

    def test_new_and_deleted_objects_select_their_collections(self):
        ws = self.sentinel_workspace()
        self.write(f"{ws}/analytic-rules/home-network/nas-login-failures.yaml", "kind: Scheduled\n")
        self.remove(f"{ws}/watchlists/break-glass-accounts.yaml")
        self.commit()
        self.assertEqual(self.select(None, "--no-expand")["units"],
                         [f"{ws}/analytic-rules", f"{ws}/watchlists"])

    def test_object_moved_between_subfolders_selects_its_collection(self):
        ws = self.sentinel_workspace()
        self.git("mv", f"{ws}/analytic-rules/entra-id/break-glass-sign-in.yaml",
                 f"{ws}/analytic-rules/break-glass-sign-in.yaml")
        self.commit()
        r = self.select(None, "--no-expand")
        self.assertEqual(r["units"], [f"{ws}/analytic-rules"])
        self.assertEqual(r["renamed"], [])

    def test_logic_app_files_select_their_unit(self):
        ws = self.sentinel_workspace()
        self.write(f"{ws}/logic-apps-standard/la-soar/workflows/revoke/workflow.json", "{}\n")
        self.write(f"{ws}/logic-apps-consumption/pb-revoke/definition.json", "{}\n")
        self.commit()
        self.assertEqual(self.select(None, "--no-expand")["units"],
                         [f"{ws}/logic-apps-consumption/pb-revoke", f"{ws}/logic-apps-standard/la-soar"])

    def test_workspace_file_selects_every_unit_below(self):
        ws = self.sentinel_workspace()
        self.write(f"{ws}/workspace.hcl", "resource_group changed\n")
        self.commit()
        self.assertEqual(self.select(None, "--no-expand")["units"], [
            ws, f"{ws}/analytic-rules", f"{ws}/logic-apps-consumption/pb-revoke",
            f"{ws}/logic-apps-standard/la-soar", f"{ws}/watchlists"])

    def test_schema_alone_selects_nothing(self):
        self.write(".github/schemas/sentinel-analytic-rules.schema.json", "{}\n")
        self.commit()
        r = self.select()
        self.assertFalse(r["all"])
        self.assertEqual(r["units"], [])

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

    def test_device_file_selects_every_unit_below(self):
        self.write("infrastructure/router/device.hcl")
        self.write("infrastructure/router/dns/terragrunt.hcl")
        self.write("infrastructure/router/wifi/terragrunt.hcl")
        self.base = self.commit("router")
        self.write("infrastructure/router/device.hcl", "hosturl changed\n")
        self.commit()
        self.assertEqual(self.select(None, "--no-expand")["units"],
                         ["infrastructure/router/dns", "infrastructure/router/wifi"])

    def test_excluded_units_are_reported(self):
        self.write("tenant/groups/terragrunt.hcl")
        self.commit()
        r = self.select(None, excluded=["tenant/memberships"])
        self.assertEqual(r["excluded"], ["tenant/memberships"])
        self.assertEqual(r["units"], ["tenant/groups"])
        self.assertEqual(self.select()["excluded"], [])

    def test_context_file_selects_a_unit_elsewhere_that_reads_it(self):
        self.write("mg-corp/sub-a/subscription.hcl")
        self.commit()
        units = self.select(None, "--no-expand")["units"]
        self.assertIn("mg-corp/sub-b/weu/rg-y/key-vault", units)
        self.assertIn("mg-corp/sub-a/weu/rg-x/sentinel", units)
        self.assertNotIn("mg-corp/sub-b/weu/rg-y", units)

    def test_common_unit_file_selects_only_the_units_including_it(self):
        self.write("_common/units/key-vault.hcl")
        self.commit()
        r = self.select(None, "--no-expand")
        self.assertFalse(r["all"])
        self.assertEqual(r["units"], ["mg-corp/sub-a/weu/rg-x/key-vault",
                                      "mg-corp/sub-b/weu/rg-y/key-vault"])

    def test_common_dependency_selects_the_units_including_it(self):
        self.write("_common/deps/groups.hcl")
        self.commit()
        self.assertEqual(self.select()["units"], ["tenant/groups", "tenant/memberships"])

    def test_common_file_nobody_reads_selects_everything(self):
        # A template root.hcl renders with templatefile(): terragrunt does not
        # report it as read, so it counts for every unit.
        self.write("_common/templates/providers.tf.tmpl")
        self.commit()
        self.assertTrue(self.select()["all"])

    def test_deleted_common_file_selects_everything(self):
        self.remove("_common/units/key-vault.hcl")
        self.commit()
        self.assertTrue(self.select()["all"])

    def test_common_file_every_unit_reads_is_the_whole_tree(self):
        self.write("_common/units/key-vault.hcl")
        self.commit()
        includes = {u: {"unit": "_common/units/key-vault.hcl"} for u in (
            "tenant/groups", "tenant/memberships", "mg-corp", "mg-corp/sub-a",
            "mg-corp/sub-a/weu/rg-x", "mg-corp/sub-a/weu/rg-x/key-vault",
            "mg-corp/sub-a/weu/rg-x/key-vault/certificate", "mg-corp/sub-a/weu/rg-x/sentinel",
            "mg-corp/sub-b", "mg-corp/sub-b/weu/rg-y", "mg-corp/sub-b/weu/rg-y/key-vault")}
        self.assertTrue(self.select(includes=includes)["all"])

    def test_root_and_account_select_everything(self):
        for path in ("root.hcl", "account.hcl"):
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

    def test_push_of_workflows_alone_selects_nothing(self):
        # The pull request planned the whole tree with them; the push has nothing to apply.
        self.write(".github/workflows/ci.yml")
        self.write(".github/tool-versions.env", "x\n")
        self.commit()
        self.assertTrue(self.select()["all"])
        r = self.select(None, "--push")
        self.assertFalse(r["all"])
        self.assertEqual(r["units"], [])

    def test_push_of_workflows_with_a_unit_selects_the_unit(self):
        self.write(".github/workflows/ci.yml")
        self.write("tenant/groups/terragrunt.hcl")
        self.commit()
        self.assertEqual(self.select(None, "--push", "--no-expand")["units"], ["tenant/groups"])

    def test_push_of_root_hcl_still_selects_everything(self):
        self.write("root.hcl")
        self.commit()
        self.assertTrue(self.select(None, "--push")["all"])

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

    def test_move_into_infrastructure_keeps_the_state(self):
        (self.repo / "infrastructure").mkdir()
        self.git("mv", "mg-corp", "infrastructure/mg-corp")
        self.git("mv", "tenant", "infrastructure/tenant")
        self.commit()
        r = self.select(None, "--no-expand")
        self.assertEqual(r["renamed"], [])
        self.assertEqual(r["deleted"], [])
        self.assertIn(["mg-corp/sub-a", "infrastructure/mg-corp/sub-a"], r["moved"])
        self.assertTrue(r["all"])

    def test_one_unit_into_infrastructure_with_a_new_content(self):
        # Rewritten beyond git's rename detection: still the same key.
        self.remove("tenant/groups")
        self.write("infrastructure/tenant/groups/terragrunt.hcl", "include unit\n" * 40)
        self.commit()
        r = self.select(None, "--no-expand")
        self.assertEqual(r["moved"], [["tenant/groups", "infrastructure/tenant/groups"]])
        self.assertEqual(r["units"], ["infrastructure/tenant/groups"])
        self.assertEqual(r["deleted"], [])
        self.assertEqual(r["renamed"], [])

    def test_listed_move_is_planned_under_the_new_path(self):
        self.git("mv", "tenant/groups", "tenant/entra-groups")
        self.commit()
        r = self.select(None, "--no-expand",
                        moves={"moves": [{"from": "tenant/groups", "to": "tenant/entra-groups"}]})
        self.assertEqual(r["moved"], [["tenant/groups", "tenant/entra-groups"]])
        self.assertEqual(r["renamed"], [])
        self.assertEqual(r["deleted"], [])
        self.assertEqual(r["units"], ["tenant/entra-groups"])

    def test_listed_move_names_keys_without_infrastructure(self):
        self.remove("mg-corp/sub-a/weu/rg-x/key-vault/certificate")
        self.write("infrastructure/mg-corp/sub-a/weu/rg-x/key-vault-certificates/terragrunt.hcl")
        self.commit()
        r = self.select(None, "--no-expand", moves={"moves": [{
            "from": "mg-corp/sub-a/weu/rg-x/key-vault/certificate",
            "to": "mg-corp/sub-a/weu/rg-x/key-vault-certificates"}]})
        self.assertEqual(r["moved"], [["mg-corp/sub-a/weu/rg-x/key-vault/certificate",
                                       "infrastructure/mg-corp/sub-a/weu/rg-x/key-vault-certificates"]])
        self.assertEqual(r["deleted"], [])

    def test_listed_merge_into_an_existing_unit(self):
        # The certificate goes into the key vault's state; nothing is destroyed.
        self.remove("mg-corp/sub-a/weu/rg-x/key-vault/certificate")
        self.write("mg-corp/sub-a/weu/rg-x/key-vault/terragrunt.hcl", "certificates\n")
        self.commit()
        r = self.select(None, "--no-expand", moves={"moves": [{
            "from": "mg-corp/sub-a/weu/rg-x/key-vault/certificate",
            "to": "mg-corp/sub-a/weu/rg-x/key-vault"}]})
        self.assertEqual(r["moved"], [["mg-corp/sub-a/weu/rg-x/key-vault/certificate",
                                       "mg-corp/sub-a/weu/rg-x/key-vault"]])
        self.assertEqual(r["deleted"], [])
        self.assertEqual(r["units"], ["mg-corp/sub-a/weu/rg-x/key-vault"])

    def test_two_units_into_one_new_unit(self):
        # A rule's state is copied, the endpoint imported: neither is destroyed.
        self.remove("mg-corp/sub-a/weu/rg-x/key-vault/certificate")
        self.remove("mg-corp/sub-a/weu/rg-x/sentinel")
        self.write("infrastructure/mg-corp/sub-a/weu/rg-x/data-collection/terragrunt.hcl")
        self.commit()
        r = self.select(None, "--no-expand", moves={"moves": [
            {"from": "mg-corp/sub-a/weu/rg-x/key-vault/certificate", "to": "mg-corp/sub-a/weu/rg-x/data-collection"},
            {"from": "mg-corp/sub-a/weu/rg-x/sentinel", "to": "mg-corp/sub-a/weu/rg-x/data-collection"}]})
        self.assertEqual(r["deleted"], [])
        self.assertEqual(len(r["moved"]), 2)
        self.assertEqual(r["units"], ["infrastructure/mg-corp/sub-a/weu/rg-x/data-collection"])

    def test_stale_move_entries_are_ignored(self):
        self.write("tenant/groups/terragrunt.hcl")
        self.commit()
        r = self.select(None, "--no-expand",
                        moves={"moves": [{"from": "old/unit", "to": "tenant/groups"}]})
        self.assertEqual(r["units"], ["tenant/groups"])
        self.assertEqual(r["moved"], [])

    def test_move_to_a_missing_unit_fails(self):
        self.remove("tenant/groups")
        self.commit()
        out = self.invoke(None, moves={"moves": [{"from": "tenant/groups", "to": "tenant/nowhere"}]})
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("tenant/nowhere", out.stderr)

    def test_invalid_moves_fail(self):
        self.write("tenant/groups/terragrunt.hcl")
        self.commit()
        for moves in ({"moves": "x"}, {"moves": [{"from": "a"}]},
                      {"moves": [{"from": "infrastructure/a", "to": "b"}]},
                      {"moves": [{"from": "a", "to": "a"}]},
                      {"moves": [{"from": "a", "to": "b"}, {"from": "a", "to": "c"}]}):
            with self.subTest(moves=moves):
                self.assertNotEqual(self.invoke(None, moves=moves).returncode, 0)

    def test_empty_moves_file_is_fine(self):
        self.write("tenant/groups/terragrunt.hcl")
        self.commit()
        for moves in (None, {}, {"moves": []}):
            with self.subTest(moves=moves):
                path = pathlib.Path(self.tmp.name, "m.json")
                path.write_text(json.dumps(moves))
                self.assertEqual(self.select(None, f"--moves={path}")["units"], ["tenant/groups"])

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
