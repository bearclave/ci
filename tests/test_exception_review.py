#!/usr/bin/env python3
"""Tests of src/exception_review.py: reasons, review dates, the four kinds.

Run: python3 -m unittest discover -s tests
"""
import datetime
import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import exception_review  # noqa: E402

TODAY = datetime.date(2026, 11, 19)


class Review(unittest.TestCase):
    def test_conventions(self):
        data = {"exceptions": [
            {"rule": "no-hardcoded-location", "path": "a.hcl", "reason": "why", "review": "2027-05-19"},
            {"rule": "tags-required", "path": "b.hcl", "reason": "why", "review": "2026-01-01"},
            {"rule": "module-source", "path": "c.hcl", "reason": "why"},
        ], "layout": {"exceptions": [{"path": "nas", "reason": "why"}]}}
        errors, warnings, rows = exception_review.review("conventions", data, TODAY)
        self.assertEqual(errors, [])
        self.assertEqual(len(warnings), 2)
        self.assertIn("due for review on 2026-01-01", warnings[0])
        self.assertIn("no review date", warnings[1])
        self.assertEqual([r[2] for r in rows], [False, True])

    def test_missing_reason_and_bad_date_fail(self):
        data = {"exceptions": [{"rule": "x", "path": "a", "reason": " ", "review": "2027-13-01"},
                               {"rule": "y", "path": "b", "reason": "why", "review": "next year"}]}
        errors, _, _ = exception_review.review("sentinel", data, TODAY)
        self.assertEqual(len(errors), 3)

    def test_sentinel_old_keys(self):
        data = {"exceptions": [{"check": "tactics", "rule": "rules/*.json", "reason": "why",
                                "review": "2027-05-19"}]}
        _, _, rows = exception_review.review("sentinel", data, TODAY)
        self.assertEqual(rows[0][0], "tactics rules/*.json")

    def test_trivy(self):
        data = {"misconfigurations": [{"id": "AVD-AZU-0013", "paths": ["a", "b"], "statement": "why",
                                       "review": "2027-05-19"},
                                      {"id": "AVD-AZU-0012", "statement": ""}]}
        errors, warnings, rows = exception_review.review("trivy", data, TODAY)
        self.assertEqual(errors, ["exception AVD-AZU-0012 has no reason"])
        self.assertEqual(rows, [("AVD-AZU-0013 (2 paths)", "2027-05-19", False)])

    def test_gitleaks(self):
        with tempfile.TemporaryDirectory() as d:
            path = pathlib.Path(d) / ".gitleaks.toml"
            path.write_text('[extend]\nuseDefault = true\n\n[[allowlists]]\ndescription = """\n'
                            'A public constant.\nreview: 2026-11-01\n"""\nregexes = [\'\'\'x\'\'\']\n', encoding="utf-8")
            data = exception_review.load("gitleaks", str(path))
        errors, warnings, rows = exception_review.review("gitleaks", data, TODAY)
        self.assertEqual(errors, [])
        self.assertIn("due for review on 2026-11-01", warnings[0])
        self.assertEqual(rows, [("allowlist 1", "2026-11-01", True)])

    def test_main_exit_code(self):
        with tempfile.TemporaryDirectory() as d:
            good = pathlib.Path(d) / "good.json"
            good.write_text(json.dumps({"exceptions": [{"rule": "x", "path": "a", "reason": "why",
                                                        "review": "2020-01-01"}]}))
            bad = pathlib.Path(d) / "bad.json"
            bad.write_text(json.dumps({"misconfigurations": [{"id": "X"}]}))
            self.assertEqual(exception_review.main(["--today", "2026-11-19", f"conventions=c.yaml:{good}"]), 0)
            self.assertEqual(exception_review.main(["--today", "2026-11-19", f"trivy=t.yaml:{bad}"]), 1)


if __name__ == "__main__":
    unittest.main()
