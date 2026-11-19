#!/usr/bin/env python3
"""Tests of src/pr_check.py: the title rules and how a bot's pull request is
judged. `gh` is replaced by a stub, so nothing talks to GitHub.

Run: python3 -m unittest discover -s tests
"""
import contextlib
import io
import json
import os
import pathlib
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import pr_check  # noqa: E402

LONG = "deps: bump actions/upload-artifact from 7.0.1 to 7.0.2 in the actions group"
BODY = "\n".join(f"## {s}\n\nnone\n" for s in pr_check.SECTIONS)


def fake_gh(*args, check=True):
    if args[:2] == ("api", "--paginate"):
        return json.dumps([[{"filename": "a.yml", "additions": 1, "deletions": 1}]])
    if args[0] == "api":
        raise RuntimeError("no .gitattributes")
    return ""


def run(author: str, title: str, branch: str, body: str = BODY) -> tuple:
    env = {"GITHUB_REPOSITORY": "bearclave/ci", "PR_NUMBER": "1", "PR_TITLE": title,
           "PR_BODY": body, "PR_BRANCH": branch, "PR_AUTHOR": author, "PR_ASSIGNEES": "",
           "PR_LABELS": "", "PR_HEAD_SHA": "abc"}
    out = io.StringIO()
    with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(pr_check, "gh", fake_gh), \
            contextlib.redirect_stdout(out):
        code = pr_check.main()
    return code, out.getvalue()


class TitleTest(unittest.TestCase):
    def test_length_is_separate_from_the_format(self):
        self.assertEqual(pr_check.title_problems(LONG), [])
        self.assertEqual(len(pr_check.title_too_long(LONG)), 1)
        self.assertEqual(pr_check.title_too_long("fix: x"), [])

    def test_bad_format_is_reported(self):
        self.assertTrue(pr_check.title_problems("Bump actions"))


class BotTest(unittest.TestCase):
    def test_long_title_of_dependabot_only_warns(self):
        code, out = run("dependabot[bot]", LONG, "dependabot/github_actions/actions-1", body="")
        self.assertEqual(code, 0)
        self.assertIn("::warning::The title is 75 characters", out)
        self.assertNotIn("::error::", out)

    def test_long_title_of_an_app_only_warns(self):
        code, out = run("bearclave-bot[bot]", LONG, "deps/bump-upload-artifact")
        self.assertEqual(code, 0)
        self.assertIn("### Warnings", out)

    def test_bad_format_of_a_bot_still_fails(self):
        code, out = run("dependabot[bot]", "Bump actions/checkout", "dependabot/github_actions/x")
        self.assertEqual(code, 1)
        self.assertIn("::error::The title", out)


class PersonTest(unittest.TestCase):
    def test_long_title_of_a_person_fails(self):
        code, out = run("matej-hrabalek", LONG, "deps/bump-upload-artifact")
        self.assertEqual(code, 1)
        self.assertIn("::error::The title is 75 characters", out)

    def test_short_title_passes(self):
        code, _ = run("matej-hrabalek", "fix(pr): warn only on long bot titles", "fix/bot-title-length")
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
