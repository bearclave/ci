#!/usr/bin/env python3
"""Tests of src/site_checks.py: the config, the headers, the pages and their links.

Run: python3 -m unittest discover -s tests
"""
import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import site_checks  # noqa: E402

HEADERS = {
    "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; "
                               "frame-ancestors 'none'",
    "Strict-Transport-Security": "max-age=63072000",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
}
PAGE = ('<!DOCTYPE html>\n<html lang="en">\n<head><meta charset="utf-8"><title>T</title>'
        '<link rel="stylesheet" href="/site.css"></head>\n<body>\n<p id="top">x\n{body}\n</body>\n</html>\n')


class SiteChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.app = self.root / "src"
        self.app.mkdir()
        (self.app / "site.css").write_text("")

    def tearDown(self):
        self.tmp.cleanup()

    def site(self, config=None, pages=None):
        config = config if config is not None else {"routes": [{"route": "/about", "rewrite": "/about.html"}],
                                                    "globalHeaders": HEADERS}
        (self.app / "staticwebapp.config.json").write_text(json.dumps(config))
        for name, body in (pages or {"index.html": '<a href="/about#team">a</a>',
                                     "about.html": '<h2 id="team">t</h2>'}).items():
            (self.app / name).parent.mkdir(parents=True, exist_ok=True)
            (self.app / name).write_text(PAGE.format(body=body))
        findings = site_checks.Findings()
        context = site_checks.check_config(str(self.app), "src", findings)
        site_checks.check_pages(str(self.app), str(self.root), context, findings)
        return sorted((c, m) for _, _, c, m in findings.items)

    def test_clean_site(self):
        self.assertEqual(self.site(), [])

    def test_config(self):
        found = self.site({"routes": [{"route": "/a", "rewrite": "/a.html"}, {"route": "/A/"},
                                      {"route": "/b", "rewrite": "/none.html"}],
                           "globalHeaders": {**HEADERS, "Content-Security-Policy": "script-src 'self' 'unsafe-inline'"},
                           "responseOverrides": {"404": {"rewrite": "/404.html"}}},
                          {"a.html": ""})
        checks = [c for c, _ in found]
        self.assertEqual(checks.count("config"), 3)
        self.assertEqual(checks.count("headers"), 2)

    def test_pages(self):
        found = self.site(pages={"index.html": '<div style="x" onclick="y"><script>z</script>'
                                               '<a href="#">a</a><a href="/gone">b</a><a href="#nope">c</a>'
                                               '<a href="about.html#nope">d</a><span id="top"></span>',
                                 "about.html": ""})
        self.assertEqual([c for c, _ in found], ["csp", "csp", "csp", "html", "html", "links", "links", "links",
                                                 "links"])

    def test_relative_links_and_folders(self):
        found = self.site(pages={"en/index.html": '<a href="../about.html">a</a><a href="/en/">b</a>',
                                 "about.html": ""})
        self.assertEqual(found, [])


if __name__ == "__main__":
    unittest.main()
