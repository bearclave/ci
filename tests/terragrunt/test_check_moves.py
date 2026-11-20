#!/usr/bin/env python3
"""Runs check-moves.py over hand-written JSON plans of moved units.

Run: python3 tests/terragrunt/test_check_moves.py
"""
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "src" / "terragrunt" / "check-moves.py"
MOVED = [["mg/rg/key-vault/certificate", "infrastructure/mg/rg/key-vault"]]


def change(address, *actions, mode="managed"):
    return {"address": address, "mode": mode, "change": {"actions": list(actions)}}


class CheckMoves(unittest.TestCase):
    def check(self, changes, moved=MOVED, write=True, prior=None):
        with tempfile.TemporaryDirectory() as tmp:
            if write:
                unit = pathlib.Path(tmp, "json", moved[0][1])
                unit.mkdir(parents=True)
                plan = {"resource_changes": changes}
                if prior:
                    plan["prior_state"] = prior
                unit.joinpath("tfplan.json").write_text(json.dumps(plan))
            selection = pathlib.Path(tmp, "selection.json")
            selection.write_text(json.dumps({"moved": moved}))
            return subprocess.run([sys.executable, str(SCRIPT), str(selection),
                                   str(pathlib.Path(tmp, "json"))],
                                  capture_output=True, text=True)

    def test_import_update_and_no_op_pass(self):
        out = self.check([change("a.this", "no-op"), change("b.this", "update"),
                          change("c.this", "read", mode="data")])
        self.assertEqual(out.returncode, 0, out.stdout)

    def test_create_fails(self):
        out = self.check([change("azurerm_key_vault.this", "create")])
        self.assertEqual(out.returncode, 1)
        self.assertIn("infrastructure/mg/rg/key-vault (moved from mg/rg/key-vault/certificate): "
                      "azurerm_key_vault.this would create", out.stdout)

    def test_destroy_and_replace_fail(self):
        out = self.check([change("a.this", "delete"), change("b.this", "delete", "create")])
        self.assertEqual(out.returncode, 1)
        self.assertEqual(out.stdout.count("::error"), 2)

    def test_unit_with_its_state_may_create(self):
        # The state was moved: a new resource in the unit is an ordinary create.
        prior = {"values": {"root_module": {"child_modules": [{"resources": [
            {"address": "module.kv.azurerm_key_vault.this", "mode": "managed"}]}]}}}
        out = self.check([change("module.rbac.azurerm_role_assignment.active[\"x\"]", "create")],
                         prior=prior)
        self.assertEqual(out.returncode, 0, out.stdout)

    def test_state_with_data_sources_only_counts_as_empty(self):
        prior = {"values": {"root_module": {"resources": [{"address": "data.x.this", "mode": "data"}]}}}
        out = self.check([change("a.this", "create")], prior=prior)
        self.assertEqual(out.returncode, 1)

    def test_unit_of_another_job_is_skipped(self):
        self.assertEqual(self.check([], write=False).returncode, 0)

    def test_same_key_move_may_create(self):
        # Into infrastructure/ with the same key: the state did not move.
        out = self.check([change("module.lock.azurerm_management_lock.this[0]", "create")],
                         moved=[["mg/rg-x", "infrastructure/mg/rg-x"]])
        self.assertEqual(out.returncode, 0, out.stdout)

    def test_a_unit_not_moved_may_create(self):
        out = self.check([change("a.this", "create")], moved=[["x", "y"]], write=False)
        self.assertEqual(out.returncode, 0)


if __name__ == "__main__":
    unittest.main()
