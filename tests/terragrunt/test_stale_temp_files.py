#!/usr/bin/env python3
"""Tests of src/terragrunt/stale-temp-files.py: imports.hcl and moves.hcl older than their pull request.

Run: python3 -m unittest discover -s tests
"""
import contextlib
import importlib.util
import io
import os
import pathlib
import subprocess
import tempfile
import unittest

SRC = pathlib.Path(__file__).resolve().parents[2] / "src" / "terragrunt" / "stale-temp-files.py"
spec = importlib.util.spec_from_file_location("stale_temp_files", SRC)
stf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stf)


class Stale(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = os.getcwd()
        os.chdir(self.tmp.name)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "t")
        self.git("config", "commit.gpgsign", "false")

    def tearDown(self):
        os.chdir(self.old)
        self.tmp.cleanup()

    def git(self, *args):
        return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout.strip()

    def commit(self, files):
        for path, text in files.items():
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            pathlib.Path(path).write_text(text)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "c")
        return self.git("rev-parse", "HEAD")

    def test_a_file_of_the_change_being_applied_is_not_stale(self):
        base = self.commit({"infrastructure/a/terragrunt.hcl": "x\n"})
        self.commit({"infrastructure/a/imports.hcl": "locals {}\n"})
        self.assertEqual(stf.stale(base), [])

    def test_a_file_older_than_the_base_is_stale(self):
        first = self.commit({"infrastructure/a/imports.hcl": "locals {}\n", "infrastructure/b/moves.hcl": "locals {}\n"})
        base = self.commit({"README.md": "x\n"})
        self.assertEqual(sorted(p for p, _ in stf.stale(base)),
                         ["infrastructure/a/imports.hcl", "infrastructure/b/moves.hcl"])
        self.assertEqual({c for _, c in stf.stale(base)}, {first})

    def test_moves_hcl_counts_like_imports_hcl(self):
        self.commit({"infrastructure/a/moves.hcl": "locals {}\n"})
        self.assertEqual([p for p, _ in stf.stale("HEAD")], ["infrastructure/a/moves.hcl"])

    def test_other_hcl_files_are_ignored(self):
        self.commit({"infrastructure/a/terragrunt.hcl": "x\n", "infrastructure/a/not-imports.hcl": "x\n"})
        self.assertEqual(stf.stale("HEAD"), [])

    def test_unknown_base_says_nothing(self):
        self.commit({"infrastructure/a/imports.hcl": "x\n"})
        self.assertEqual(stf.stale("0" * 40), [])

    def test_warning_names_file_and_line(self):
        self.commit({"infrastructure/a/imports.hcl": "x\n"})
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(stf.main([]), 0)
        self.assertRegex(out.getvalue(), r"^::warning file=infrastructure/a/imports.hcl,line=1,title=Temporary file::")


if __name__ == "__main__":
    unittest.main()
