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

            def run(*args):
                return subprocess.run([sys.executable, str(SCRIPT), str(root), *args],
                                       capture_output=True, text=True)

            self.assertEqual(run("--check").returncode, 1)
            self.assertEqual(run().returncode, 0)
            catalog = (root / "docs" / "catalog.md").read_text()
            self.assertIn("| [`azure/key-vault`](../azure/key-vault/README.md) | A key vault with its "
                          "certificates. | `name` | `id` |", catalog)
            self.assertEqual(run("--check").returncode, 0)


if __name__ == "__main__":
    unittest.main()
