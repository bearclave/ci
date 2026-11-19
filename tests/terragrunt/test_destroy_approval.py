#!/usr/bin/env python3
"""Tests of the decisions in destroy-approval.py, without the API.

Run: python3 tests/terragrunt/test_destroy_approval.py
"""
import importlib.util
import pathlib
import unittest

SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "src" / "terragrunt" / "destroy-approval.py"
spec = importlib.util.spec_from_file_location("destroy_approval", SCRIPT)
da = importlib.util.module_from_spec(spec)
spec.loader.exec_module(da)

HEAD = "abc"
PLANNED = "2026-11-19T10:00:00Z"


def review(login, state, at, commit=HEAD, kind="User"):
    return {"user": {"login": login, "type": kind}, "state": state, "submitted_at": at, "commit_id": commit}


class Approval(unittest.TestCase):
    def check(self, reviews):
        return da.approval(reviews, "bearclave-bot[bot]", HEAD, PLANNED)

    def test_approval_after_the_plan(self):
        self.assertIsNotNone(self.check([review("matej", "APPROVED", "2026-11-19T10:05:00Z")]))

    def test_approval_before_the_plan(self):
        self.assertIsNone(self.check([review("matej", "APPROVED", "2026-11-19T09:59:59Z")]))

    def test_approval_at_the_same_second(self):
        self.assertIsNone(self.check([review("matej", "APPROVED", PLANNED)]))

    def test_no_review(self):
        self.assertIsNone(self.check([]))

    def test_approval_of_an_older_commit(self):
        self.assertIsNone(self.check([review("matej", "APPROVED", "2026-11-19T10:05:00Z", commit="old")]))

    def test_later_request_for_changes_wins(self):
        self.assertIsNone(self.check([
            review("matej", "APPROVED", "2026-11-19T10:05:00Z"),
            review("matej", "CHANGES_REQUESTED", "2026-11-19T10:06:00Z"),
        ]))

    def test_comment_after_approval_keeps_it(self):
        self.assertIsNotNone(self.check([
            review("matej", "APPROVED", "2026-11-19T10:05:00Z"),
            review("matej", "COMMENTED", "2026-11-19T10:06:00Z"),
        ]))

    def test_dismissed(self):
        self.assertIsNone(self.check([review("matej", "DISMISSED", "2026-11-19T10:05:00Z")]))

    def test_second_approval_after_the_plan(self):
        self.assertIsNotNone(self.check([
            review("matej", "APPROVED", "2026-11-19T09:00:00Z"),
            review("matej", "APPROVED", "2026-11-19T10:05:00Z"),
        ]))

    def test_author_and_bots_do_not_count(self):
        self.assertIsNone(self.check([
            review("bearclave-bot[bot]", "APPROVED", "2026-11-19T10:05:00Z", kind="Bot"),
            review("other[bot]", "APPROVED", "2026-11-19T10:05:00Z", kind="Bot"),
        ]))
        self.assertIsNone(da.approval([review("matej", "APPROVED", "2026-11-19T10:05:00Z")], "matej", HEAD, PLANNED))


class Lookups(unittest.TestCase):
    def test_last_pull_request_run(self):
        runs = [{"id": 1, "event": "pull_request"}, {"id": 3, "event": "pull_request_review"},
                {"id": 2, "event": "pull_request"}]
        self.assertEqual(da.last_run(runs)["id"], 2)
        self.assertIsNone(da.last_run([]))

    def test_destroy_job(self):
        jobs = [{"name": "pipeline / plan"}, {"name": "pipeline / destroy / approved", "id": 7}]
        self.assertEqual(da.destroy_job(jobs)["id"], 7)
        self.assertIsNone(da.destroy_job(jobs[:1]))

    def test_approved_only_through_the_step(self):
        ok = {"conclusion": "success", "steps": [{"name": da.STEP, "conclusion": "success"}]}
        none = {"conclusion": "success", "steps": [{"name": da.STEP, "conclusion": "skipped"},
                                                   {"name": "No destroys in the plan", "conclusion": "success"}]}
        failed = {"conclusion": "failure", "steps": [{"name": da.STEP, "conclusion": "failure"}]}
        self.assertTrue(da.approved_by_step(ok))
        self.assertFalse(da.approved_by_step(none))
        self.assertFalse(da.approved_by_step(failed))
        self.assertFalse(da.approved_by_step(None))

    def test_merged_pull(self):
        pulls = [{"number": 1, "merged_at": None, "merge_commit_sha": "s"},
                 {"number": 2, "merged_at": "2026-11-19T10:00:00Z", "merge_commit_sha": "s"}]
        self.assertEqual(da.merged_pull(pulls, "s")["number"], 2)
        self.assertIsNone(da.merged_pull(pulls, "t"))


if __name__ == "__main__":
    unittest.main()
