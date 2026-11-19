"""Tests of src/project_sync.py against a fake GitHub.

Run: python3 -m unittest discover -s tests
"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
import project_sync as ps  # noqa: E402


def page(nodes, cursor=None):
    return {"nodes": nodes, "pageInfo": {"hasNextPage": cursor is not None, "endCursor": cursor}}


class FakeGitHub:
    """Projects 1-4, repositories with a group property, open issues and PRs."""

    def __init__(self, repos, items=None, open_items=None):
        self.repos = repos                    # name -> (archived, group)
        self.items = items or {}              # project number -> [content ids]
        self.open = open_items or {}          # (repo, kind) -> [(id, number)]
        self.added = []

    def rest(self, path):
        repo = path.split("/")[2]
        group = self.repos[repo][1]
        return [{"property_name": "group", "value": group}] if group else []

    def graphql(self, query, **v):
        if query is ps.Q_PROJECTS:
            return {"organization": {"projectsV2": page([
                {"id": f"P{n}", "number": n, "title": f"T{n}", "closed": False} for n in (1, 2, 3, 4)])}}
        if query is ps.Q_ITEMS:
            ids = self.items.get(int(v["id"][1:]), [])
            # Two pages, so the cursor is followed.
            if v["after"] is None and len(ids) > 1:
                return {"node": {"items": page([{"content": {"id": ids[0]}}, {"content": None}], "c1")}}
            rest = ids[1:] if v["after"] else ids
            return {"node": {"items": page([{"content": {"id": i}} for i in rest])}}
        if query is ps.Q_REPOS:
            return {"organization": {"repositories": page(
                [{"name": n, "isArchived": a} for n, (a, _) in self.repos.items()])}}
        if query == ps.M_ADD:
            self.added.append((v["project"], v["content"]))
            return {}
        kind = "issues" if "issues(" in query else "pullRequests"
        nodes = [{"id": i, "number": n} for i, n in self.open.get((v["repo"], kind), [])]
        return {"repository": {kind: page(nodes)}}


class Sync(unittest.TestCase):
    def test_adds_missing_items_to_the_project_of_the_group(self):
        gh = FakeGitHub(
            repos={"iac-a": (False, "iac"), "app-b": (False, "app"), "ci": (False, "platform")},
            items={1: ["I1", "I9"]},
            open_items={("iac-a", "issues"): [("I1", 1), ("I2", 2)],
                        ("iac-a", "pullRequests"): [("PR3", 3)],
                        ("app-b", "pullRequests"): [("PR4", 4)]})
        summary = ps.sync(gh, dry_run=False)
        self.assertEqual(gh.added, [("P1", "I2"), ("P1", "PR3"), ("P2", "PR4")])
        self.assertEqual(summary["added"], {"T1": 2, "T2": 1, "T3": 0, "T4": 0})
        self.assertEqual(summary["present"], 1)

    def test_second_run_adds_nothing(self):
        gh = FakeGitHub(repos={"iac-a": (False, "iac")}, items={1: ["I1", "I2"]},
                        open_items={("iac-a", "issues"): [("I1", 1), ("I2", 2)]})
        summary = ps.sync(gh, dry_run=False)
        self.assertEqual(gh.added, [])
        self.assertEqual(summary["present"], 2)

    def test_dry_run_adds_nothing(self):
        gh = FakeGitHub(repos={"ci": (False, "platform")}, open_items={("ci", "issues"): [("I1", 1)]})
        summary = ps.sync(gh, dry_run=True)
        self.assertEqual(gh.added, [])
        self.assertEqual(summary["added"]["T4"], 1)

    def test_skips_archived_and_ungrouped_and_flags_unknown_group(self):
        gh = FakeGitHub(
            repos={"old": (True, "iac"), "template-x": (False, None), "odd": (False, "other")},
            open_items={("old", "issues"): [("I1", 1)], ("template-x", "issues"): [("I2", 2)],
                        ("odd", "issues"): [("I3", 3)]})
        summary = ps.sync(gh, dry_run=False)
        self.assertEqual(gh.added, [])
        self.assertEqual(summary["skipped"], ["old (archived)", "template-x (no group)"])
        self.assertEqual(summary["unknown_group"], ["odd (other)"])
        self.assertIn("Group without a Project", ps.report(summary, False))


if __name__ == "__main__":
    unittest.main()
