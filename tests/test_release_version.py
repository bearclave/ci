#!/usr/bin/env python3
"""Tests of src/release_version.py: the bump from the commit subjects and the
next version, against a throwaway git repository.

Run: python3 -m unittest discover -s tests
"""
import contextlib
import io
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import release_version as rv  # noqa: E402


class BumpTest(unittest.TestCase):
    def test_types(self):
        self.assertEqual(rv.bump_of("feat(api): add x (#4)", ""), "minor")
        self.assertEqual(rv.bump_of("perf: faster (#4)", ""), "patch")
        self.assertEqual(rv.bump_of("chore: tidy (#4)", ""), "none")
        self.assertEqual(rv.bump_of("Doplnit stav po migraci", ""), "other")

    def test_breaking(self):
        self.assertEqual(rv.bump_of("feat(x)!: rename input", ""), "major")
        self.assertEqual(rv.bump_of("fix: y", "## Why\n\nBREAKING CHANGE: z"), "major")
        self.assertEqual(rv.bump_of("fix: y", "mentions a BREAKING CHANGE: in prose"), "patch")

    def test_next_version(self):
        self.assertEqual(rv.next_version("v2.1.0", ["patch", "patch"]), ("v2.1.1", "patch"))
        self.assertEqual(rv.next_version("v2.1.0", ["minor", "major"]), ("v3.0.0", "major"))
        self.assertEqual(rv.next_version("v0.1.0", ["major"]), ("v0.2.0", "minor"))
        self.assertEqual(rv.next_version("v2.1.0", ["none"]), ("", "none"))
        self.assertEqual(rv.next_version("", ["none"]), ("v1.0.0", "first"))

    def test_notes_group_like_release_yml(self):
        lines = rv.notes([("feat!: a (#1)", ""), ("feat: b (#2)", ""), ("deps: c (#3)", ""), ("docs: d (#4)", "")])
        text = "\n".join(lines)
        self.assertLess(text.index("Breaking changes"), text.index("- feat!: a"))
        self.assertEqual(text.count("feat!: a"), 1)
        self.assertIn("### Dependencies\n- deps: c (#3)", text)
        self.assertIn("### Other\n- docs: d (#4)", text)


class RepositoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cwd = os.getcwd()
        os.chdir(self.tmp.name)
        self.git("init", "-q", "-b", "main")

    def tearDown(self):
        os.chdir(self.cwd)
        self.tmp.cleanup()

    def git(self, *args):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false",
                        "-c", "tag.gpgsign=false", *args], check=True, capture_output=True)

    def commit(self, subject, body=""):
        self.git("commit", "-q", "--allow-empty", "-m", subject, *(["-m", body] if body else []))

    def run_main(self, wanted=""):
        out_file = pathlib.Path(self.tmp.name) / "out"
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"GITHUB_OUTPUT": str(out_file)}), contextlib.redirect_stdout(out):
            code = rv.main(wanted)
        outputs = dict(x.split("=", 1) for x in out_file.read_text().splitlines()) if out_file.exists() else {}
        return code, outputs, out.getvalue()

    def test_since_the_highest_tag(self):
        self.commit("feat: a (#1)")
        self.git("tag", "-a", "v1.9.0", "-m", "v1.9.0")
        self.commit("fix: b (#2)")
        self.git("tag", "-a", "v1.10.0", "-m", "v1.10.0")
        self.commit("feat: c (#3)")
        self.commit("docs: d (#4)")
        code, out, _ = self.run_main()
        self.assertEqual((code, out["version"], out["previous"], out["bump"]), (0, "v1.11.0", "v1.10.0", "minor"))

    def test_no_release_needed(self):
        self.commit("feat: a (#1)")
        self.git("tag", "v1.0.0")
        self.commit("ci: b (#2)")
        code, out, text = self.run_main()
        self.assertEqual((code, out["version"]), (0, ""))
        self.assertIn("no release needed", text)

    def test_first_release_and_old_history(self):
        self.commit("Initial commit")
        self.commit("feat: a (#1)")
        code, out, text = self.run_main()
        self.assertEqual((code, out["version"], out["previous"]), (0, "v1.0.0", ""))
        self.assertIn("::warning::Not a Conventional Commit, not counted: Initial commit", text)

    def test_given_version(self):
        self.commit("fix: a (#1)")
        self.git("tag", "v1.2.0")
        self.commit("fix: b (#2)")
        self.assertEqual(self.run_main("v2.0.0")[1]["version"], "v2.0.0")
        self.assertEqual(self.run_main("v1.1.0")[0], 1)
        self.assertEqual(self.run_main("2.0")[0], 1)
        self.assertEqual(self.run_main("v2.0.0\n")[0], 1)

    def test_given_version_already_on_the_commit(self):
        # A rerun of promote.yml after it tagged: the tag is at HEAD, not above it.
        self.commit("fix: a (#1)")
        self.git("tag", "v1.2.0")
        self.commit("fix: b (#2)")
        self.git("tag", "-a", "v1.2.1", "-m", "v1.2.1")
        self.assertEqual(self.run_main("v1.2.1")[0], 0)
        self.assertEqual(self.run_main("v1.2.0")[0], 1)


if __name__ == "__main__":
    unittest.main()
