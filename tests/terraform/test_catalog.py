#!/usr/bin/env python3
"""Runs src/terraform/catalog.py over a small module tree (needs terraform-docs).

Run: python3 tests/terraform/test_catalog.py
"""
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "src" / "terraform" / "catalog.py"
README = "# azure/key-vault\n\n## What it manages\n\nA key vault\nwith its certificates.\n\n## Why\n\nx\n"


@unittest.skipUnless(shutil.which("terraform-docs"), "terraform-docs is not installed")
class Catalog(unittest.TestCase):
    def test_write_and_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            mod = root / "azure" / "key-vault"
            mod.mkdir(parents=True)
            (mod / "README.md").write_text(README)
            (mod / "variables.tf").write_text('variable "name" {\n  type        = string\n'
                                              '  description = "Name."\n}\n')
            (mod / "outputs.tf").write_text('output "id" {\n  value       = "x"\n  description = "ID."\n}\n')

            intune = root / "intune" / "compliance-policies"
            intune.mkdir(parents=True)
            (intune / "README.md").write_text(README.replace("A key vault\nwith its certificates.", "Compliance."))
            (intune / "variables.tf").write_text((mod / "variables.tf").read_text())
            (intune / "outputs.tf").write_text((mod / "outputs.tf").read_text())
            empty = root / "_empty"
            empty.mkdir()
            (empty / "README.md").write_text("# _empty\n\n## What it manages\n\nNothing.\n\n")
            (empty / "versions.tf").write_text("terraform {}\n")

            def run(*args):
                return subprocess.run([sys.executable, str(SCRIPT), str(root), *args],
                                       capture_output=True, text=True)

            self.assertEqual(run("--check").returncode, 1)
            self.assertEqual(run().returncode, 0)
            catalog = (root / "docs" / "catalog.md").read_text()
            self.assertIn("| [`azure/key-vault`](../azure/key-vault/README.md) | A key vault with its "
                          "certificates. | `name` | `id` |", catalog)
            self.assertIn("## intune/", catalog)
            self.assertLess(catalog.index("## azure/"), catalog.index("## intune/"))
            self.assertNotIn("_empty", catalog)
            self.assertEqual(run("--check").returncode, 0)

    def test_modules_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            mod = root / "modules" / "azure" / "key-vault"
            mod.mkdir(parents=True)
            (mod / "README.md").write_text(README)
            (mod / "variables.tf").write_text('variable "name" {\n  type        = string\n'
                                              '  description = "Name."\n}\n')
            (mod / "outputs.tf").write_text('output "id" {\n  value       = "x"\n  description = "ID."\n}\n')
            subprocess.run([sys.executable, str(SCRIPT), str(root)], check=True)
            catalog = (root / "docs" / "catalog.md").read_text()
            self.assertIn("| [`modules/azure/key-vault`](../modules/azure/key-vault/README.md) |", catalog)
            self.assertIn("`${modules}//modules/<platform>/<module>?ref=<modules_ref>`", catalog)


if __name__ == "__main__":
    unittest.main()
