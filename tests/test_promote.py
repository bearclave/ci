#!/usr/bin/env python3
"""Tests of src/promote.py: which commit may go to production and what a
promotion does, without GitHub.

Run: python3 -m unittest discover -s tests
"""
import datetime
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import promote  # noqa: E402

SHA = "a" * 40
PROD = "b" * 40
NOW = datetime.datetime(2026, 11, 20, 12, 0, tzinfo=datetime.timezone.utc)


def run(branch="staging", event="push", status="completed", conclusion="success", created="2026-11-20T10:00:00Z",
        run_id=1):
    return {"id": run_id, "head_branch": branch, "event": event, "status": status, "conclusion": conclusion,
            "created_at": created, "html_url": f"https://github.com/x/runs/{run_id}"}


class PickRun(unittest.TestCase):
    def test_newest_push_or_manual_run_of_the_branch(self):
        runs = [run(run_id=1, created="2026-11-20T10:00:00Z"),
                run(run_id=2, event="workflow_dispatch", created="2026-11-20T11:00:00Z"),
                run(run_id=3, branch="prod", created="2026-11-20T12:00:00Z"),
                run(run_id=4, event="pull_request", created="2026-11-20T13:00:00Z")]
        self.assertEqual(promote.pick_run(runs, "staging")["id"], 2)

    def test_none(self):
        self.assertIsNone(promote.pick_run([run(branch="feat/x")], "staging"))


class CheckRun(unittest.TestCase):
    def test_success(self):
        promote.check_run(run(), SHA, "staging", "ci.yml")

    def test_refuses(self):
        for name, r in {"missing": None, "running": run(status="in_progress", conclusion=None),
                        "failed": run(conclusion="failure"), "cancelled": run(conclusion="cancelled")}.items():
            with self.subTest(name), self.assertRaises(promote.Refused):
                promote.check_run(r, SHA, "staging", "ci.yml")

    def test_newer_failed_run_wins(self):
        # A failed rerun after a green one: staging is not known good.
        runs = [run(run_id=1), run(run_id=2, event="workflow_dispatch", conclusion="failure",
                                   created="2026-11-20T11:00:00Z")]
        with self.assertRaises(promote.Refused):
            promote.check_run(promote.pick_run(runs, "staging"), SHA, "staging", "ci.yml")


class CheckArtifact(unittest.TestCase):
    def art(self, name="site", expired=False, expires="2027-02-18T10:00:00Z"):
        return {"name": name, "expired": expired, "expires_at": expires, "created_at": "2026-11-20T10:00:00Z"}

    def test_live(self):
        self.assertEqual(promote.check_artifact([self.art()], "site", NOW)["name"], "site")

    def test_refuses(self):
        cases = {"missing": [self.art(name="other")], "expired": [self.art(expired=True)],
                 "about to expire": [self.art(expires="2026-11-20T12:30:00Z")]}
        for name, arts in cases.items():
            with self.subTest(name), self.assertRaises(promote.Refused):
                promote.check_artifact(arts, "site", NOW)


class Plan(unittest.TestCase):
    def plan(self, sha=SHA, prod=PROD, ancestor=True, on_staging=True, tags=(), version="v1.2.0",
             existing=("v1.1.0",)):
        return promote.plan(sha, prod, ancestor, on_staging, list(tags), version, list(existing))

    def test_fast_forward_tag_release(self):
        self.assertEqual(self.plan(), {"move": "fast-forward", "tag": "v1.2.0", "release": "v1.2.0"})

    def test_first_promotion_creates_prod(self):
        self.assertEqual(self.plan(prod="", existing=(), version="v1.0.0")["move"], "create")

    def test_rerun_after_the_move(self):
        self.assertEqual(self.plan(prod=SHA), {"move": None, "tag": "v1.2.0", "release": "v1.2.0"})

    def test_rerun_after_the_tag(self):
        self.assertEqual(self.plan(prod=SHA, tags=("v1.2.0",), version=""),
                         {"move": None, "tag": None, "release": "v1.2.0"})

    def test_already_released_commit_moves_without_a_new_tag(self):
        self.assertEqual(self.plan(tags=("v1.1.0",), version=""),
                         {"move": "fast-forward", "tag": None, "release": "v1.1.0"})

    def test_refuses(self):
        cases = {
            "not a sha": {"sha": "abc"},
            "not on staging": {"on_staging": False},
            "prod not an ancestor (backwards)": {"ancestor": False},
            "nothing to release": {"version": ""},
            "bad version": {"version": "1.2.0"},
            "tag exists elsewhere": {"existing": ("v1.2.0",)},
        }
        for name, kwargs in cases.items():
            with self.subTest(name), self.assertRaises(promote.Refused):
                self.plan(**kwargs)


if __name__ == "__main__":
    unittest.main()
