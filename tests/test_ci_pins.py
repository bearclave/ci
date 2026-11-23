#!/usr/bin/env python3
"""Tests of src/ci_pins.py without the network.

Run: python3 tests/test_ci_pins.py
"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
import ci_pins  # noqa: E402

CI = """jobs:
  security:
    uses: bearclave/ci/.github/workflows/security.yml@v2.2.0
  pipeline:
    uses: bearclave/ci/.github/workflows/terragrunt.yml@v2.1.0
  review:
    uses: bearclave/ci/.github/workflows/destroy-review.yml@v2.3.0
  old:
    uses: bearclave/ci/.github/workflows/conventions.yml@v1.4.0
  follow:
    uses: bearclave/ci/.github/workflows/pr.yml@v2
  other:
    uses: bearclave/other/.github/workflows/x.yml@v2.0.0
"""


def rel(tag, **kw):
    return {"tag_name": tag, **kw}


class Bump(unittest.TestCase):
    def test_older_pins_of_the_same_major(self):
        out, old = ci_pins.bump(CI, "v2.3.0")
        self.assertEqual(old, ["v2.1.0", "v2.2.0"])
        self.assertIn("security.yml@v2.3.0", out)
        self.assertIn("terragrunt.yml@v2.3.0", out)
        self.assertIn("destroy-review.yml@v2.3.0", out)
        self.assertIn("conventions.yml@v1.4.0", out)
        self.assertIn("pr.yml@v2\n", out)
        self.assertIn("bearclave/other/.github/workflows/x.yml@v2.0.0", out)

    def test_never_backwards(self):
        out, old = ci_pins.bump(CI, "v2.0.5")
        self.assertEqual(old, [])
        self.assertEqual(out, CI)

    def test_version_ordering_is_numeric(self):
        out, old = ci_pins.bump("uses: bearclave/ci/.github/workflows/a.yml@v2.9.0\n", "v2.10.0")
        self.assertEqual(old, ["v2.9.0"])

    def test_new_major_is_not_a_bump(self):
        out, old = ci_pins.bump(CI, "v3.0.0")
        self.assertEqual(old, [])


class Release(unittest.TestCase):
    def test_newest_passes(self):
        ci_pins.check_release("v2.3.0", rel("v2.3.0"), [rel("v2.2.0"), rel("v2.3.0"), rel("v3.0.0")])

    def test_refusals(self):
        with self.assertRaises(SystemExit):
            ci_pins.check_release("v2.2.0", rel("v2.2.0"), [rel("v2.2.0"), rel("v2.3.0")])
        with self.assertRaises(SystemExit):
            ci_pins.check_release("v2.4.0", rel("v2.4.0", prerelease=True), [])
        with self.assertRaises(SystemExit):
            ci_pins.check_release("v2", rel("v2"), [])

    def test_unpublished_newer_does_not_count(self):
        ci_pins.check_release("v2.3.0", rel("v2.3.0"), [rel("v2.4.0", draft=True), rel("v2.5.0", prerelease=True)])


class PullRequest(unittest.TestCase):
    def test_title_and_sections(self):
        self.assertLessEqual(len(ci_pins.title("v2.10.10")), 72)
        text = ci_pins.body("v2.3.0", "https://x", ["v2.2.0"], [".github/workflows/ci.yml"])
        for section in ("Why", "What changes", "Impact", "Verification", "Review", "Links"):
            self.assertIn(f"## {section}\n", text)


class Unchanged(unittest.TestCase):
    """A bump is skipped when none of the workflows a repository calls differs."""

    def setUp(self):
        self.calls = []
        self.files = {"filename": ".github/workflows/container.yml"}
        self._request = ci_pins.gh.request
        ci_pins.gh.request = self.fake

    def tearDown(self):
        ci_pins.gh.request = self._request

    def fake(self, method, path, token="", payload=None, **kw):
        self.calls.append(path)
        return {"files": [self.files]}

    def test_called_lists_only_what_a_bump_moves(self):
        self.assertEqual(ci_pins.called(CI, "v2.3.0"),
                         {"security.yml": "v2.2.0", "terragrunt.yml": "v2.1.0"})

    def test_a_workflow_it_does_not_call_changed(self):
        self.assertFalse(ci_pins.calls_a_changed_workflow([CI], "v2.3.0", "t", {}))

    def test_a_workflow_it_calls_changed(self):
        self.files = {"filename": ".github/workflows/terragrunt.yml"}
        self.assertTrue(ci_pins.calls_a_changed_workflow([CI], "v2.3.0", "t", {}))

    def test_a_source_file_alone_is_no_change(self):
        # The workflows are generated, so a change that reaches a caller is in them.
        self.files = {"filename": "src/pr_check.py"}
        self.assertFalse(ci_pins.calls_a_changed_workflow([CI], "v2.3.0", "t", {}))

    def test_a_truncated_comparison_counts_as_a_change(self):
        ci_pins.gh.request = lambda *a, **k: {"files": [{"filename": "x"}] * ci_pins.COMPARE_LIMIT}
        self.assertTrue(ci_pins.calls_a_changed_workflow([CI], "v2.3.0", "t", {}))

    def test_an_unreadable_comparison_counts_as_a_change(self):
        def boom(*a, **k):
            raise ci_pins.gh.NotFound("x")
        ci_pins.gh.request = boom
        self.assertTrue(ci_pins.calls_a_changed_workflow([CI], "v2.3.0", "t", {}))

    def test_a_comparison_is_asked_once(self):
        cache = {}
        ci_pins.calls_a_changed_workflow([CI, CI], "v2.3.0", "t", cache)
        self.assertEqual(len(self.calls), 2)   # v2.1.0...v2.3.0 and v2.2.0...v2.3.0

    def test_nothing_to_bump_is_no_change(self):
        self.assertFalse(ci_pins.calls_a_changed_workflow(["uses: x@v1\n"], "v2.3.0", "t", {}))


class Signed(unittest.TestCase):
    def check(self, ref_type, verified):
        ref = {"object": {"type": ref_type, "sha": "abc"}}
        tag_object = {"verification": {"verified": verified}, "object": {"type": "commit", "sha": "c"}}
        calls = {"/repos/bearclave/ci/git/ref/tags/v2.3.0": ref, "/repos/bearclave/ci/git/tags/abc": tag_object}
        original = ci_pins.gh.request
        ci_pins.gh.request = lambda method, path, token="", **k: calls[path]
        try:
            ci_pins.check_signed("v2.3.0", rel("v2.3.0"), "t")
        finally:
            ci_pins.gh.request = original

    def test_signed_annotated_tag_passes(self):
        self.check("tag", True)

    def test_lightweight_tag_is_refused(self):
        with self.assertRaises(SystemExit):
            self.check("commit", False)

    def test_unsigned_tag_is_refused(self):
        with self.assertRaises(SystemExit):
            self.check("tag", False)


if __name__ == "__main__":
    unittest.main()
