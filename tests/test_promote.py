#!/usr/bin/env python3
"""Tests of src/promote.py: which commit may go to production and what a
promotion does, without GitHub.

Run: python3 -m unittest discover -s tests
"""
import datetime
import pathlib
import sys
import unittest
from unittest import mock

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
    def test_newest_successful_push_or_manual_run_of_the_branch(self):
        runs = [run(run_id=1, created="2026-11-20T10:00:00Z"),
                run(run_id=2, event="workflow_dispatch", created="2026-11-20T11:00:00Z"),
                run(run_id=3, branch="prod", created="2026-11-20T12:00:00Z"),
                run(run_id=4, event="pull_request", created="2026-11-20T13:00:00Z"),
                run(run_id=5, conclusion="failure", created="2026-11-20T14:00:00Z"),
                run(run_id=6, status="in_progress", conclusion=None, created="2026-11-20T15:00:00Z")]
        self.assertEqual(promote.pick_run(runs, "staging")["id"], 2)

    def test_none(self):
        self.assertIsNone(promote.pick_run([run(branch="feat/x"), run(conclusion="cancelled")], "staging"))


class CheckRun(unittest.TestCase):
    def test_found(self):
        promote.check_run(run(), SHA, "staging", "ci.yml")

    def test_missing(self):
        with self.assertRaises(promote.Refused):
            promote.check_run(None, SHA, "staging", "ci.yml")


class CheckJob(unittest.TestCase):
    def test_deployed(self):
        promote.check_job([{"name": "pipeline / site", "conclusion": "success"},
                           {"name": "pipeline / deploy-staging", "conclusion": "success"}], "deploy-staging")

    def test_refuses(self):
        cases = {"missing": [{"name": "pipeline / site", "conclusion": "success"}],
                 "skipped": [{"name": "pipeline / deploy-staging", "conclusion": "skipped"}],
                 "similar name": [{"name": "pipeline / deploy-staging-x", "conclusion": "success"}]}
        for name, jobs in cases.items():
            with self.subTest(name), self.assertRaises(promote.Refused):
                promote.check_job(jobs, "deploy-staging")


class ProdRun(unittest.TestCase):
    def test_states(self):
        self.assertEqual(promote.prod_state([], "prod")[0], "none")
        self.assertEqual(promote.prod_state([run(branch="staging")], "prod")[0], "none")
        self.assertEqual(promote.prod_state([run(branch="prod", status="in_progress", conclusion=None)], "prod")[0],
                         "pending")
        self.assertEqual(promote.prod_state([run(branch="prod", conclusion="failure")], "prod")[0], "failure")

    def test_newest_counts(self):
        # A failed deployment, then a manual run that succeeded.
        runs = [run(branch="prod", conclusion="failure", run_id=1, created="2026-11-20T10:00:00Z"),
                run(branch="prod", event="workflow_dispatch", run_id=2, created="2026-11-20T11:00:00Z")]
        self.assertEqual(promote.prod_state(runs, "prod"), ("success", runs[1]))

    def test_wait(self):
        answers = [[], [run(branch="prod", status="in_progress", conclusion=None)], [run(branch="prod")]]
        with mock.patch.object(promote, "runs_of", side_effect=lambda *a: answers.pop(0)):
            got = promote.wait_for_prod("o/r", "ci.yml", SHA, "prod", "t", 5, sleep=lambda s: None)
        self.assertEqual(got["conclusion"], "success")

    def test_wait_refuses_a_failed_run_and_a_timeout(self):
        with mock.patch.object(promote, "runs_of", return_value=[run(branch="prod", conclusion="failure")]):
            with self.assertRaises(promote.Refused):
                promote.wait_for_prod("o/r", "ci.yml", SHA, "prod", "t", 5, sleep=lambda s: None)
        with mock.patch.object(promote, "runs_of", return_value=[]):
            with self.assertRaises(promote.Refused):
                promote.wait_for_prod("o/r", "ci.yml", SHA, "prod", "t", 0, sleep=lambda s: None)


class Container(unittest.TestCase):
    """container.yml: the deploy job is `deploy` in both runs, and the run of
    the release tag that promote pushes (head_branch vX.Y.Z) counts for
    neither branch."""

    def test_tag_run_counts_for_no_branch(self):
        tag_run = run(branch="v1.2.0", run_id=9, created="2026-11-20T23:00:00Z")
        self.assertIsNone(promote.pick_run([tag_run], "staging"))
        self.assertEqual(promote.prod_state([tag_run], "prod")[0], "none")
        prod_run = run(branch="prod", run_id=2)
        self.assertEqual(promote.prod_state([prod_run, tag_run], "prod"), ("success", prod_run))

    def test_deploy_job(self):
        prod_jobs = [{"name": "pipeline / build", "conclusion": "skipped"},
                     {"name": "pipeline / prod-image", "conclusion": "success"},
                     {"name": "pipeline / version-tag", "conclusion": "skipped"},
                     {"name": "pipeline / deploy", "conclusion": "success"},
                     {"name": "pipeline / gate", "conclusion": "success"}]
        promote.check_job(prod_jobs, "deploy")
        skipped = [dict(j, conclusion="skipped") if j["name"].endswith("/ deploy") else j for j in prod_jobs]
        with self.assertRaises(promote.Refused):
            promote.check_job(skipped, "deploy")


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


class WaitMinutes(unittest.TestCase):
    def test_bounds(self):
        self.assertEqual(promote.wait_minutes("20"), 20)
        self.assertEqual(promote.wait_minutes(30), 30)
        for bad in ("31", "-1", "x", None):
            with self.subTest(bad), self.assertRaises(promote.Refused):
                promote.wait_minutes(bad)


class ProdJob(unittest.TestCase):
    def test_green_run_with_a_skipped_deployment_is_not_released(self):
        with self.assertRaises(promote.Refused):
            promote.check_job([{"name": "pipeline / site", "conclusion": "skipped"},
                               {"name": "pipeline / deploy-prod", "conclusion": "skipped"}], "deploy-prod")
        promote.check_job([{"name": "pipeline / deploy-prod", "conclusion": "success"}], "deploy-prod")


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

    def test_already_released_commit_refuses_another_version(self):
        with self.assertRaises(promote.Refused):
            self.plan(prod=SHA, tags=("v1.2.0",), version="v1.3.0")
        self.assertEqual(self.plan(prod=SHA, tags=("v1.2.0",), version="v1.2.0")["release"], "v1.2.0")

    def test_already_released_commit_moves_without_a_new_tag(self):
        self.assertEqual(self.plan(tags=("v1.1.0",), version=""),
                         {"move": "fast-forward", "tag": None, "release": "v1.1.0"})

    def test_refuses(self):
        cases = {
            "not a sha": {"sha": "abc"},
            "sha with a newline": {"sha": SHA + "\n"},
            "not on staging": {"on_staging": False},
            "prod not an ancestor (backwards)": {"ancestor": False},
            "nothing to release": {"version": ""},
            "bad version": {"version": "1.2.0"},
            "version with a newline": {"version": "v1.2.0\n"},
            "tag exists elsewhere": {"existing": ("v1.2.0",)},
        }
        for name, kwargs in cases.items():
            with self.subTest(name), self.assertRaises(promote.Refused):
                self.plan(**kwargs)


if __name__ == "__main__":
    unittest.main()
