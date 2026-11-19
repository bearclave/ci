#!/usr/bin/env python3
"""Tests of src/major_tag.py: which releases the major tag may move to.

Run: python3 -m unittest discover -s tests
"""
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import major_tag  # noqa: E402

COMMIT = "c" * 40


def fixture(tag="v2.3.0", draft=False, prerelease=False, ref_type="tag", verified=True, target="commit"):
    release = {"tag_name": tag, "draft": draft, "prerelease": prerelease}
    ref = {"object": {"type": ref_type, "sha": "t" * 40}}
    tag_object = {"verification": {"verified": verified, "reason": "valid" if verified else "unsigned"},
                  "object": {"type": target, "sha": COMMIT}}
    return tag, release, ref, tag_object


class CheckRelease(unittest.TestCase):
    def test_signed_published_release(self):
        self.assertEqual(major_tag.check_release(*fixture()), COMMIT)

    def test_refuses(self):
        cases = {
            "pattern": fixture(tag="v2.3"),
            "suffix": fixture(tag="v2.3.0-rc1"),
            "draft": fixture(draft=True),
            "prerelease": fixture(prerelease=True),
            "lightweight": fixture(ref_type="commit"),
            "unsigned": fixture(verified=False),
            "not a commit": fixture(target="tree"),
        }
        for name, args in cases.items():
            with self.subTest(name), self.assertRaises(major_tag.Refused):
                major_tag.check_release(*args)

    def test_release_of_another_tag(self):
        tag, release, ref, tag_object = fixture()
        release["tag_name"] = "v2.2.0"
        with self.assertRaises(major_tag.Refused):
            major_tag.check_release(tag, release, ref, tag_object)


class Newest(unittest.TestCase):
    RELEASES = [
        {"tag_name": "v2.1.0"},
        {"tag_name": "v2.10.0", "prerelease": True},
        {"tag_name": "v2.2.1"},
        {"tag_name": "v3.0.0"},
        {"tag_name": "v2.4.0", "draft": True},
        {"tag_name": "latest"},
    ]

    def test_numeric_order_within_the_major(self):
        self.assertEqual(major_tag.newest("v2.2.1", self.RELEASES), "v2.2.1")
        self.assertEqual(major_tag.newest("v2.3.0", self.RELEASES), "v2.3.0")

    def test_older_backport_stays_behind(self):
        self.assertEqual(major_tag.newest("v2.1.0", self.RELEASES), "v2.2.1")

    def test_version(self):
        self.assertEqual(major_tag.version("v2.10.0"), (2, 10, 0))
        self.assertIsNone(major_tag.version("v2"))
        self.assertIsNone(major_tag.version("2.1.0"))


if __name__ == "__main__":
    unittest.main()
