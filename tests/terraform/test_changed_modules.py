#!/usr/bin/env python3
"""Tests of src/terraform/changed-modules.py: which modules a pull request has to check.

Run: python3 -m unittest discover -s tests
"""
import importlib.util
import pathlib
import unittest

SRC = pathlib.Path(__file__).resolve().parents[2] / "src" / "terraform" / "changed-modules.py"
spec = importlib.util.spec_from_file_location("changed_modules", SRC)
cm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cm)

FILES = [
    "README.md", ".tflint.hcl", ".github/workflows/ci.yml", "docs/catalog.md",
    "azure/_common/tags.tf", "azure/key-vault/main.tf", "azure/key-vault/README.md",
    "azure/key-vault/tests/basic.tftest.hcl", "azure/storage/main.tf", "azure/storage/README.md",
    "azure/wrapper/main.tf", "entra/groups/main.tf", "entra/groups/README.md", ".terraform-docs.yml",
]
SOURCES = {"azure/wrapper": 'module "kv" {\n  source = "../key-vault"\n}\n'}


def select(changes):
    return cm.select(FILES, changes, lambda m: SOURCES.get(m, ""))


class Select(unittest.TestCase):
    def test_no_base_is_everything(self):
        chosen, why = select(None)
        self.assertIn("no base", why)
        self.assertEqual(chosen["validate"], ["azure/_common", "azure/key-vault", "azure/storage",
                                              "azure/wrapper", "entra/groups"])
        self.assertEqual(chosen["test"], ["azure/key-vault"])
        self.assertEqual(chosen["readme"], ["azure/key-vault", "azure/storage", "entra/groups"])

    def test_one_module(self):
        chosen, why = select(["entra/groups/main.tf"])
        self.assertIsNone(why)
        self.assertEqual(chosen["validate"], ["entra/groups"])
        self.assertEqual(chosen["test"], [])
        self.assertEqual(chosen["readme"], ["entra/groups"])

    def test_a_file_below_a_module_belongs_to_it(self):
        chosen, _ = select(["azure/key-vault/tests/basic.tftest.hcl"])
        self.assertIn("azure/key-vault", chosen["test"])

    def test_the_caller_of_a_changed_module_is_checked_too(self):
        chosen, _ = select(["azure/key-vault/main.tf"])
        self.assertEqual(chosen["validate"], ["azure/key-vault", "azure/wrapper"])

    def test_shared_blocks_and_tools_run_everything(self):
        for path in ("azure/_common/tags.tf", ".github/workflows/ci.yml", ".tflint.hcl", ".terraform-docs.yml",
                     ".github/tool-versions.env", "versions.tf"):
            chosen, why = select([path])
            self.assertTrue(why, path)
            self.assertEqual(len(chosen["validate"]), 5, path)

    def test_markdown_and_docs_outside_the_modules_check_none(self):
        chosen, why = select(["README.md", "docs/catalog.md", ".claude/rules/x.md"])
        self.assertIsNone(why)
        self.assertEqual(chosen["validate"], [])

    def test_a_deleted_module_runs_everything(self):
        chosen, why = select(["azure/old/main.tf"])
        self.assertTrue(why)


if __name__ == "__main__":
    unittest.main()
