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
PAGE = ('<!DOCTYPE html>\n<html lang="en">\n<head><meta charset="utf-8"><title>T</title>{head}'
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

    def site(self, config=None, pages=None, heads=None, sitemap=None):
        config = config if config is not None else {"routes": [{"route": "/about", "rewrite": "/about.html"}],
                                                    "globalHeaders": HEADERS}
        (self.app / "staticwebapp.config.json").write_text(json.dumps(config))
        for name, body in (pages or {"index.html": '<a href="/about#team">a</a>',
                                     "about.html": '<h2 id="team">t</h2>'}).items():
            (self.app / name).parent.mkdir(parents=True, exist_ok=True)
            head = (heads or {}).get(name, '<meta name="robots" content="noindex">')
            (self.app / name).write_text(PAGE.format(head=head, body=body))
        if sitemap is not None:
            (self.app / "sitemap.xml").write_text(sitemap)
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
                                                 "links", "seo"])

    def test_relative_links_and_folders(self):
        found = self.site(pages={"en/index.html": '<a href="../about.html">a</a><a href="/en/">b</a>',
                                 "about.html": ""})
        self.assertEqual(found, [("seo", 'href="../about.html" is relative; links start with /')])

    def test_seo(self):
        alt = ('<link rel="canonical" href="https://x.test/{0}">'
               '<link rel="alternate" hreflang="en" href="https://x.test/{0}">'
               '<link rel="alternate" hreflang="x-default" href="https://x.test/{1}">')
        sitemap = "<urlset><url><loc>https://x.test/</loc></url><url><loc>https://x.test/gone</loc></url></urlset>"
        pages = {"index.html": "", "about.html": "", "other.html": "", "404.html": ""}
        heads = {"index.html": alt.format("", ""),
                 "about.html": '<link rel="canonical" href="https://x.test/about.html">'
                               '<link rel="alternate" hreflang="cs" href="https://x.test/missing.html">',
                 "other.html": ""}
        found = self.site(pages=pages, heads=heads, sitemap=sitemap)
        messages = [m for c, m in found if c == "seo"]
        self.assertEqual(len(messages), 6, messages)
        self.assertTrue(any("no canonical" in m for m in messages))
        self.assertTrue(any("page itself" in m for m in messages))
        self.assertTrue(any("x-default" in m for m in messages))
        self.assertTrue(any("missing.html" in m for m in messages))
        self.assertTrue(any("gone leads to no page" in m for m in messages))
        self.assertTrue(any("about.html (canonical" in m for m in messages))


if __name__ == "__main__":
    unittest.main()
