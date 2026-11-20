#!/usr/bin/env python3
"""Tests of src/housekeeping.py: what counts as stale, the report, and that
the issue is updated in place and closed when nothing is stale. `gh` is
replaced by a stub that records the calls.

Run: python3 -m unittest discover -s tests
"""
import contextlib
import datetime as dt
import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import housekeeping as hk  # noqa: E402


def days_ago(n: int) -> str:
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=n)).strftime("%Y-%m-%dT%H:%M:%SZ")


def ref(name, age, open_prs=0):
    return {"name": name, "associatedPullRequests": {"totalCount": open_prs},
            "target": {"committedDate": days_ago(age)}}


def pr(number, age, labels=(), draft=False):
    return {"number": number, "title": f"feat: pr {number}", "isDraft": draft, "updatedAt": days_ago(age),
            "labels": {"nodes": [{"name": x} for x in labels]}}


def repository(refs, prs):
    return {"defaultBranchRef": {"name": "main"}, "refs": {"totalCount": len(refs), "nodes": refs},
            "pullRequests": {"totalCount": len(prs), "nodes": prs}}


class Gh:
    def __init__(self, repo, issues=()):
        self.repo, self.issues, self.calls = repo, list(issues), []

    def __call__(self, *args, check=True):
        self.calls.append(args)
        out = ""
        if args[:2] == ("api", "graphql"):
            out = json.dumps({"data": {"repository": self.repo}})
        elif args[:2] == ("issue", "list"):
            out = json.dumps(self.issues)
        elif "POST" in args:
            out = json.dumps({"number": 9})
        return subprocess.CompletedProcess(args, 0, out, "")


def run(fake, *flags):
    with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {"OWNER": "the-owner", "RUNNER_TEMP": tmp},
            clear=True), mock.patch.object(hk, "gh", fake), contextlib.redirect_stdout(io.StringIO()):
        code = hk.main(["--repo", "bearclave/x", *flags])
        body = pathlib.Path(tmp, "housekeeping.md")
        return code, body.read_text() if body.exists() else ""


class StaleTest(unittest.TestCase):
    def test_branches(self):
        repo = repository([ref("main", 400), ref("feat/old", 45), ref("feat/new", 3), ref("fix/with-pr", 90, 1)], [])
        found = hk.stale_branches(repo, dt.datetime.now(dt.timezone.utc), 30)
        self.assertEqual([n for n, _ in found], ["feat/old"])

    def test_application_branches_are_not_stale(self):
        repo = repository([ref("staging", 400), ref("prod", 60), ref("feat/old", 45)], [])
        repo["defaultBranchRef"] = {"name": "staging"}
        found = hk.stale_branches(repo, dt.datetime.now(dt.timezone.utc), 30)
        self.assertEqual([n for n, _ in found], ["feat/old"])

    def test_pull_requests_and_blocked(self):
        repo = repository([], [pr(1, 20), pr(2, 2), pr(3, 40, ["status:blocked"])])
        stale, blocked = hk.stale_prs(repo, dt.datetime.now(dt.timezone.utc), 14)
        self.assertEqual(([x[0] for x in stale], [x[0] for x in blocked]), ([1], [3]))


class IssueTest(unittest.TestCase):
    def test_opens_one_issue_with_type_and_assignee(self):
        fake = Gh(repository([ref("feat/old", 45)], []))
        code, body = run(fake)
        post = [c for c in fake.calls if "POST" in c][0]
        self.assertEqual(code, 0)
        self.assertIn("type=Debt", post)
        self.assertIn("assignees[]=the-owner", post)
        self.assertIn("`feat/old`", body)

    def test_updates_the_open_issue(self):
        fake = Gh(repository([], [pr(1, 20)]), issues=[{"number": 7, "body": hk.MARKER + "\nold"}])
        run(fake)
        self.assertIn(("issue", "edit", "7"), [c[:3] for c in fake.calls])
        self.assertFalse([c for c in fake.calls if "POST" in c])

    def test_ignores_a_person_s_issue_with_the_same_title(self):
        fake = Gh(repository([ref("feat/old", 45)], []), issues=[{"number": 7, "body": "by hand"}])
        run(fake)
        self.assertTrue([c for c in fake.calls if "POST" in c])

    def test_closes_when_nothing_is_stale(self):
        fake = Gh(repository([ref("feat/new", 1)], [pr(1, 1)]), issues=[{"number": 7, "body": hk.MARKER}])
        run(fake)
        self.assertIn(("issue", "close", "7"), [c[:3] for c in fake.calls])

    def test_deletes_nothing(self):
        fake = Gh(repository([ref("feat/old", 45)], [pr(1, 60)]))
        run(fake)
        for call in fake.calls:
            self.assertNotIn("DELETE", call)
            self.assertNotIn("delete", call)
            self.assertNotEqual(call[:2], ("pr", "close"))


class OrgTest(unittest.TestCase):
    def test_every_listed_repository_is_checked(self):
        fake = Gh(repository([ref("feat/old", 45)], []))
        out = io.StringIO()
        with mock.patch.object(hk, "org_repositories", lambda: ["bearclave/a", "bearclave/b"]), \
                mock.patch.object(hk, "bot_token", lambda write: "t"), mock.patch.object(hk, "gh", fake), \
                mock.patch.dict(os.environ, {"OWNER": "o"}, clear=True), contextlib.redirect_stdout(out), \
                tempfile.TemporaryDirectory() as tmp:
            os.environ["RUNNER_TEMP"] = tmp
            code = hk.main(["--org"])
        self.assertEqual(code, 0)
        repos = [c[3] for c in fake.calls if c[:2] == ("issue", "list")]
        self.assertEqual(repos, ["bearclave/a", "bearclave/b"])

    def test_dry_run_writes_nothing(self):
        fake = Gh(repository([ref("feat/old", 45)], []))
        code, body = run(fake, "--dry-run")
        self.assertEqual(code, 0)
        self.assertFalse([c for c in fake.calls if c[0] == "issue" and c[1] != "list" or "POST" in c])


if __name__ == "__main__":
    unittest.main()
