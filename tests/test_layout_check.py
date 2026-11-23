#!/usr/bin/env python3
"""layout_check.py against src/layout.yaml, the managed copy of governance's layout.yaml.

Run: python3 -m unittest discover -s tests
"""
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import layout_check  # noqa: E402

LAYOUTS = {"current": ROOT / "src" / "layout.yaml"}


@unittest.skipUnless(shutil.which("yq"), "yq reads the YAML")
class Layouts(unittest.TestCase):
    COMMON = [".claude/CLAUDE.md", ".editorconfig", ".gitattributes"]

    def errors(self, layout, name, pipeline, paths):
        paths = self.COMMON + paths
        data = json.loads(subprocess.run(["yq", "-o=json", ".", str(layout)], capture_output=True,
                                         text=True, check=True).stdout)
        with tempfile.TemporaryDirectory() as root:
            for path in paths + [".github/workflows/ci.yml"]:
                p = pathlib.Path(root, path)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(f"uses: bearclave/ci/.github/workflows/{pipeline}.yml@v2\n"
                             if path.endswith("ci.yml") else "")
            subprocess.run(["git", "init", "-q", root], check=True)
            subprocess.run(["git", "-C", root, "add", "-A"], check=True)
            return [e[0] for e in layout_check.check(data, {}, name, root)[0]]

    def test_modules_repository(self):
        self.assertEqual(self.errors(LAYOUTS["current"], "iac-modules", "terraform-modules",
                                     ["README.md", "modules/azure/key-vault/main.tf"]), [])
        # A platform folder at the root fails: only modules/ is a folder of the layout.
        self.assertEqual(self.errors(LAYOUTS["current"], "iac-modules", "terraform-modules",
                                     ["README.md", "modules/a/b/main.tf", "azure/key-vault/main.tf"]), ["azure"])

    def test_terragrunt_repository(self):
        for layout in LAYOUTS.values():
            self.assertEqual(self.errors(layout, "iac-x", "terragrunt",
                                         ["README.md", "root.hcl", "account.hcl", "infrastructure/a/terragrunt.hcl"]), [])
            self.assertEqual(self.errors(layout, "iac-x", "terragrunt",
                                         ["README.md", "root.hcl", "account.hcl", "infrastructure/a/terragrunt.hcl",
                                          "scripts/a.sh"]), ["scripts"])


if __name__ == "__main__":
    unittest.main()
