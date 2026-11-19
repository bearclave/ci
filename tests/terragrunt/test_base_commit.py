#!/usr/bin/env python3
"""Tests of src/terragrunt/base-commit.py: which run on main a push compares with.

Run: python3 -m unittest discover -s tests
"""
import importlib.util
import pathlib
import unittest

SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "src" / "terragrunt" / "base-commit.py"
spec = importlib.util.spec_from_file_location("base_commit", SCRIPT)
base_commit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base_commit)


def run(n, conclusion, event="push"):
    return {"id": n, "head_sha": f"sha{n}", "event": event, "status": "completed", "conclusion": conclusion}


def job(name, conclusion):
    return {"name": name, "conclusion": conclusion}


class Pick(unittest.TestCase):
    def pick(self, runs, jobs=None):
        asked = []

        def jobs_of(run_id):
            asked.append(run_id)
            return (jobs or {}).get(run_id, [])

        return base_commit.pick(runs, jobs_of), asked

    def test_newest_successful_run(self):
        self.assertEqual(self.pick([run(3, "success"), run(2, "success")]), ("sha3", []))

    def test_applied_but_verification_failed_counts(self):
        # iac-azure #92: the apply went through, only verify failed.
        jobs = {93: [job("pipeline / plan", "success"), job("pipeline / apply", "success"),
                     job("pipeline / verify", "failure"), job("pipeline / gate", "failure")]}
        self.assertEqual(self.pick([run(93, "failure"), run(92, "success")], jobs)[0], "sha93")

    def test_failed_or_held_back_apply_does_not_count(self):
        jobs = {5: [job("pipeline / apply", "failure")],
                4: [job("pipeline / plan", "success"), job("pipeline / apply", "skipped"),
                    job("pipeline / gate", "failure")]}
        self.assertEqual(self.pick([run(5, "failure"), run(4, "failure"), run(3, "success")], jobs),
                         ("sha3", [5, 4]))

    def test_only_pushes_and_manual_runs_on_main(self):
        runs = [run(9, "success", "schedule"), run(8, "cancelled"), run(7, "success", "workflow_dispatch")]
        self.assertEqual(self.pick(runs), ("sha7", []))

    def test_job_of_a_workflow_without_a_caller_job(self):
        self.assertEqual(self.pick([run(2, "failure")], {2: [job("apply", "success")]})[0], "sha2")

    def test_none_found(self):
        self.assertEqual(self.pick([run(1, "failure")], {1: [job("pipeline / plan", "failure")]})[0], "")

    def test_job_lookups_are_bounded(self):
        runs = [run(n, "failure") for n in range(100, 70, -1)] + [run(1, "success")]
        found, asked = self.pick(runs)
        self.assertEqual(found, "sha1")
        self.assertEqual(len(asked), base_commit.JOB_LOOKUPS)


if __name__ == "__main__":
    unittest.main()
