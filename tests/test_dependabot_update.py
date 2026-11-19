#!/usr/bin/env python3
"""Tests of src/dependabot_update.py: which Dependabot updates may merge
themselves, from real shapes of Dependabot's commit message.

Run: python3 -m unittest discover -s tests
"""
import pathlib
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import dependabot_update as du  # noqa: E402

GROUP = """deps: bump the actions group with 2 updates

Bumps the actions group with 2 updates: actions/checkout and actions/upload-artifact.

---
updated-dependencies:
- dependency-name: actions/checkout
  dependency-version: 7.0.2
  dependency-type: direct:production
  update-type: version-update:semver-patch
  dependency-group: actions
- dependency-name: actions/upload-artifact
  dependency-version: 7.1.0
  dependency-type: direct:production
  update-type: version-update:semver-minor
  dependency-group: actions
...

Signed-off-by: dependabot[bot] <support@github.com>
"""


class UpdateTypeTest(unittest.TestCase):
    def test_group_takes_the_largest(self):
        self.assertEqual(du.update_type(GROUP), "version-update:semver-minor")
        self.assertTrue(du.eligible(GROUP))

    def test_one_major_keeps_the_group(self):
        msg = GROUP.replace("semver-minor", "semver-major")
        self.assertEqual(du.update_type(msg), "version-update:semver-major")
        self.assertFalse(du.eligible(msg))

    def test_missing_update_type(self):
        msg = GROUP.replace("  update-type: version-update:semver-patch\n", "")
        self.assertFalse(du.eligible(msg))

    def test_not_a_dependabot_message(self):
        self.assertFalse(du.eligible("fix: something"))

    def test_command_line(self):
        r = subprocess.run([sys.executable, str(ROOT / "src" / "dependabot_update.py")],
                           input=GROUP, capture_output=True, text=True)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "version-update:semver-minor"))
        r = subprocess.run([sys.executable, str(ROOT / "src" / "dependabot_update.py")],
                           input="fix: x", capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)


if __name__ == "__main__":
    unittest.main()
