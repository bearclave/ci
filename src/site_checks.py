#!/usr/bin/env python3
"""The checks every static website of the organisation passes before a deploy.

Usage: site_checks.py [<app location>]       (default: src)

What only a deployed site, or a failed deployment, would show otherwise:

  config    staticwebapp.config.json is valid JSON, no route twice (with and
            without a trailing slash is the same route, and Static Web Apps
            then rejects the whole deployment), every rewrite target exists
  headers   globalHeaders carry a Content-Security-Policy without
            'unsafe-inline' or 'unsafe-eval' for scripts, framing forbidden
            (frame-ancestors or X-Frame-Options), Strict-Transport-Security,
            X-Content-Type-Options: nosniff and a Referrer-Policy
  csp       no page needs what the policy forbids: inline scripts, event
            handler attributes, <style> blocks and style attributes, data:
            URLs - each only where the policy does not allow it
  html      every page is well formed: a doctype, <html lang>, a charset, a
            non-empty <title>, end tags that match, no id twice
  links     every internal href and src leads to a file or a route of the
            config, every #anchor to an id on its page; no empty href or "#"
  seo       internal links are root-relative (/x, never x), so a page reads
            the same under any route; every page that is not noindex has a
            canonical, and where it has hreflang links, one of them is the
            page itself (Google ignores the set otherwise), one is
            x-default, and each leads to a page; sitemap.xml lists only
            pages that exist, and every canonical

Every finding is a GitHub annotation on its file; the exit code is 1 when
there is one. What is specific to a site (its tests, generated content)
stays in the site's own `site-checks` command.
"""
import html.parser
import json
import os
import re
import sys

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
# End tags HTML lets out; they never count as a mismatch.
OPTIONAL_END = {"html", "head", "body", "p", "li", "dt", "dd", "option", "optgroup", "tr", "td", "th",
                "thead", "tbody", "tfoot", "colgroup", "caption", "rt", "rp"}
EXTERNAL = re.compile(r"^(?:[a-z][a-z0-9+.-]*:|//)", re.I)
REQUIRED_HEADERS = ("Content-Security-Policy", "Strict-Transport-Security", "X-Content-Type-Options",
                    "Referrer-Policy")


class Findings:
    def __init__(self):
        self.items = []

    def add(self, path, line, check, message):
        self.items.append((path, line, check, message))


def csp_directives(policy: str) -> dict:
    out = {}
    for part in policy.split(";"):
        words = part.split()
        if words:
            out.setdefault(words[0].lower(), [w.lower() for w in words[1:]])
    return out


def source_list(directives: dict, name: str) -> list:
    """The sources a fetch directive allows, falling back to default-src."""
    for d in (name, "script-src" if name.startswith("script-src-") else None,
              "style-src" if name.startswith("style-src-") else None, "default-src"):
        if d and d in directives:
            return directives[d]
    return ["*"]


class Page(html.parser.HTMLParser):
    """The parts of a page the checks need, with line numbers."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.doctype = False
        self.lang = None
        self.charset = False
        self.title, self.in_title = None, False
        self.stack, self.mismatches = [], []
        self.ids = {}
        self.refs = []          # (line, attribute, value)
        self.inline_scripts, self.styles, self.style_attrs, self.handlers = [], [], [], []
        self.in_script = None
        self.canonical = None   # (line, href)
        self.hreflangs = []     # (line, lang, href)
        self.noindex = False

    def handle_decl(self, decl):
        if decl.lower().startswith("doctype"):
            self.doctype = True

    def handle_starttag(self, tag, attrs):
        line = self.getpos()[0]
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "html":
            self.lang = a.get("lang") or ""
        if tag == "meta" and ("charset" in a or a.get("http-equiv", "").lower() == "content-type"):
            self.charset = True
        if tag == "title":
            self.in_title, self.title = True, self.title or ""
        if tag == "meta" and a.get("name", "").lower() == "robots" and "noindex" in a.get("content", "").lower():
            self.noindex = True
        if tag == "link":
            rel = a.get("rel", "").lower().split()
            if "canonical" in rel and self.canonical is None:
                self.canonical = (line, a.get("href", ""))
            if "alternate" in rel and "hreflang" in a:
                self.hreflangs.append((line, a["hreflang"], a.get("href", "")))
        if "id" in a:
            self.ids.setdefault(a["id"], []).append(line)
        if tag == "a" and "name" in a:
            self.ids.setdefault(a["name"], []).append(line)
        for k in ("href", "src"):
            if k in a and not (tag == "link" and k == "href" and "alternate" in a.get("rel", "").split()
                               and EXTERNAL.match(a[k])):
                self.refs.append((line, k, a[k]))
        if tag == "script":
            kind = a.get("type", "").lower()
            if "src" not in a and kind not in ("application/ld+json", "application/json", "importmap"):
                self.inline_scripts.append(line)
            self.in_script = tag
        if tag == "style":
            self.styles.append(line)
        if "style" in a:
            self.style_attrs.append(line)
        self.handlers += [(line, k) for k in a if k.startswith("on")]
        if tag not in VOID:
            self.stack.append((tag, line))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID and self.stack and self.stack[-1][0] == tag:
            self.stack.pop()

    def handle_endtag(self, tag):
        line = self.getpos()[0]
        if tag == "title":
            self.in_title = False
        if tag in VOID:
            return
        if not any(t == tag for t, _ in self.stack):
            self.mismatches.append((line, f"</{tag}> closes nothing"))
            return
        while self.stack:
            open_tag, open_line = self.stack.pop()
            if open_tag == tag:
                break
            if open_tag not in OPTIONAL_END:
                self.mismatches.append((open_line, f"<{open_tag}> is never closed (</{tag}> on line {line})"))

    def handle_data(self, data):
        if self.in_title:
            self.title += data

    def close(self):
        super().close()
        for tag, line in self.stack:
            if tag not in OPTIONAL_END:
                self.mismatches.append((line, f"<{tag}> is never closed"))


def check_config(app: str, rel: str, findings: Findings):
    path = os.path.join(app, "staticwebapp.config.json")
    where = os.path.join(rel, "staticwebapp.config.json")
    if not os.path.isfile(path):
        findings.add(where, 1, "config", "staticwebapp.config.json is missing")
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            config = json.load(fh)
    except ValueError as e:
        findings.add(where, getattr(e, "lineno", 1), "config", f"not valid JSON: {e}")
        return {}
    seen = {}
    for route in config.get("routes") or []:
        name = str(route.get("route", ""))
        key = name.rstrip("/").lower() or "/"
        if key in seen:
            findings.add(where, 1, "config", f'route "{name}" is the same route as "{seen[key]}"')
        seen[key] = name
        target = route.get("rewrite")
        if target and not target.startswith("/api") and not os.path.exists(os.path.join(app, target.lstrip("/"))):
            findings.add(where, 1, "config", f'rewrite "{name}" -> "{target}" has no file')
    for code, override in (config.get("responseOverrides") or {}).items():
        target = (override or {}).get("rewrite")
        if target and not os.path.exists(os.path.join(app, target.lstrip("/"))):
            findings.add(where, 1, "config", f'responseOverrides {code} rewrites to "{target}", which has no file')
    fallback = (config.get("navigationFallback") or {}).get("rewrite")
    if fallback and not os.path.exists(os.path.join(app, fallback.lstrip("/"))):
        findings.add(where, 1, "config", f'navigationFallback rewrites to "{fallback}", which has no file')

    headers = {k.lower(): str(v) for k, v in (config.get("globalHeaders") or {}).items()}
    for name in REQUIRED_HEADERS:
        if name.lower() not in headers:
            findings.add(where, 1, "headers", f"globalHeaders has no {name}")
    if headers.get("x-content-type-options", "nosniff").lower() != "nosniff":
        findings.add(where, 1, "headers", "X-Content-Type-Options is not nosniff")
    directives = csp_directives(headers.get("content-security-policy", ""))
    if headers.get("content-security-policy"):
        scripts = source_list(directives, "script-src")
        for bad in ("'unsafe-inline'", "'unsafe-eval'"):
            if bad in scripts:
                findings.add(where, 1, "headers", f"the Content-Security-Policy allows {bad} for scripts")
        if "*" in scripts:
            findings.add(where, 1, "headers", "the Content-Security-Policy allows scripts from anywhere")
        if "frame-ancestors" not in directives and "x-frame-options" not in headers:
            findings.add(where, 1, "headers", "framing is not forbidden (frame-ancestors or X-Frame-Options)")
    return {"config": config, "csp": directives}


def resolver(app: str, config: dict):
    routes = {}
    prefixes = []
    for route in config.get("routes") or []:
        name = str(route.get("route", ""))
        if name.endswith("/*"):
            prefixes.append(name[:-1])
        elif route.get("rewrite"):
            routes[name.rstrip("/") or "/"] = os.path.join(app, route["rewrite"].lstrip("/"))
        else:
            routes[name.rstrip("/") or "/"] = None

    def resolve(page: str, path: str):
        """(exists, the file it reaches or None)."""
        if path.startswith("/"):
            url = path
        else:
            base = "/" + os.path.relpath(os.path.dirname(page), app).replace(os.sep, "/").strip(".")
            url = os.path.normpath(os.path.join(base, path)).replace(os.sep, "/")
            url += "/" if path.endswith("/") and not url.endswith("/") else ""
        if url.startswith("/api/") or url == "/api":
            return True, None
        key = url.rstrip("/") or "/"
        if key in routes:
            return True, routes[key]
        local = os.path.join(app, url.lstrip("/"))
        if os.path.isfile(local):
            return True, local
        if os.path.isdir(local) and os.path.isfile(os.path.join(local, "index.html")):
            return True, os.path.join(local, "index.html")
        if any(url.startswith(p) for p in prefixes) and os.path.exists(local):
            return True, None
        return False, None
    return resolve


def check_pages(app: str, root: str, context: dict, findings: Findings):
    config, csp = context.get("config") or {}, context.get("csp") or {}
    pages = sorted(os.path.join(d, f) for d, _, files in os.walk(app) for f in files if f.endswith(".html"))
    parsed = {}
    for page in pages:
        p = Page()
        with open(page, encoding="utf-8") as fh:
            p.feed(fh.read())
        p.close()
        parsed[page] = p
    resolve = resolver(app, config)
    scripts, styles = source_list(csp, "script-src"), source_list(csp, "style-src")
    attr_styles = source_list(csp, "style-src-attr")
    images = source_list(csp, "img-src")
    for page, p in parsed.items():
        where = os.path.relpath(page, root)
        if not p.doctype:
            findings.add(where, 1, "html", "no <!DOCTYPE html>")
        if not p.lang:
            findings.add(where, 1, "html", "<html> has no lang")
        if not p.charset:
            findings.add(where, 1, "html", "no <meta charset>")
        if not (p.title or "").strip():
            findings.add(where, 1, "html", "no <title>, or an empty one")
        for line, message in p.mismatches:
            findings.add(where, line, "html", message)
        for name, lines in p.ids.items():
            if len(lines) > 1:
                findings.add(where, lines[1], "html", f'id "{name}" is used {len(lines)} times')
        if csp:
            if "'unsafe-inline'" not in scripts:
                for line in p.inline_scripts:
                    findings.add(where, line, "csp", "an inline <script>; the policy allows no inline scripts")
                for line, attr in p.handlers:
                    findings.add(where, line, "csp", f"an event handler attribute ({attr}); the policy forbids it")
            if "'unsafe-inline'" not in styles:
                for line in p.styles:
                    findings.add(where, line, "csp", "a <style> block; the policy allows no inline styles")
            if "'unsafe-inline'" not in attr_styles:
                for line in p.style_attrs:
                    findings.add(where, line, "csp", "a style attribute; the policy allows no inline styles")
        for line, attr, value in p.refs:
            value = value.strip()
            if value in ("", "#"):
                findings.add(where, line, "links", f'{attr}="{value}" leads nowhere')
                continue
            if value.lower().startswith("data:"):
                if csp and "data:" not in (images if attr == "src" else source_list(csp, "default-src")):
                    findings.add(where, line, "csp", f"a data: URL in {attr}; the policy does not allow data:")
                continue
            if EXTERNAL.match(value):
                continue
            if not value.startswith(("/", "#")):
                findings.add(where, line, "seo", f'{attr}="{value}" is relative; links start with /')
            base, _, anchor = value.partition("#")
            path = base.split("?", 1)[0]
            if path == "":
                if anchor and anchor not in p.ids:
                    findings.add(where, line, "links", f'#{anchor} is not an id on this page')
                continue
            ok, target = resolve(page, path)
            if not ok:
                findings.add(where, line, "links", f'{attr}="{value}" leads to no file and no route')
            elif anchor and target and target.endswith(".html") and target in parsed \
                    and anchor not in parsed[target].ids:
                findings.add(where, line, "links", f'{value}: #{anchor} is not an id on that page')
    check_seo(app, root, parsed, resolve, findings)
    return len(pages)


ORIGIN = re.compile(r"^https?://[^/]+", re.I)


def check_seo(app: str, root: str, parsed: dict, resolve, findings: Findings):
    canonicals = []
    for page, p in parsed.items():
        if p.noindex:
            continue
        where = os.path.relpath(page, root)
        if not p.canonical or not p.canonical[1].strip():
            findings.add(where, 1, "seo", "no canonical link")
            continue
        canonicals.append((where, p.canonical[0], p.canonical[1]))
        if not p.hreflangs:
            continue
        line = p.hreflangs[0][0]
        if p.canonical[1] not in [h for _, _, h in p.hreflangs]:
            findings.add(where, line, "seo", "no hreflang link to the page itself; Google ignores the set")
        if "x-default" not in [lang.lower() for _, lang, _ in p.hreflangs]:
            findings.add(where, line, "seo", "no hreflang x-default")
        for hline, lang, href in p.hreflangs:
            if not resolve(page, ORIGIN.sub("", href) or "/")[0]:
                findings.add(where, hline, "seo", f'hreflang "{lang}" -> {href} leads to no page')
    sitemap = os.path.join(app, "sitemap.xml")
    if not os.path.isfile(sitemap):
        return
    where = os.path.relpath(sitemap, root)
    with open(sitemap, encoding="utf-8") as fh:
        text = fh.read()
    locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", text)
    index = os.path.join(app, "index.html")
    for loc in locs:
        if not resolve(index, ORIGIN.sub("", loc) or "/")[0]:
            findings.add(where, 1, "seo", f"{loc} leads to no page")
    for page_where, line, canonical in canonicals:
        if canonical not in locs:
            findings.add(where, 1, "seo", f"{canonical} (canonical of {page_where}) is not in the sitemap")


def escape(text: str) -> str:
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def main(app: str = "src", root: str = ".") -> int:
    app_path = os.path.normpath(os.path.join(root, app))
    rel = os.path.relpath(app_path, root)
    findings = Findings()
    context = check_config(app_path, rel, findings)
    count = check_pages(app_path, root, context, findings)
    for path, line, check, message in findings.items:
        print(f"::error file={path},line={line},title=site {check}::{escape(message)}")
    lines = ["## Site checks", "", f"{count} pages in `{rel}`: "
             + (f"{len(findings.items)} findings." if findings.items else "everything holds.")]
    if findings.items:
        lines += ["", "| Check | Where | What |", "|---|---|---|"]
        lines += [f"| {c} | `{p}:{n}` | {m.replace('|', '/')} |" for p, n, c, m in findings.items]
    text = "\n".join(lines) + "\n"
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as fh:
            fh.write(text)
    else:
        print(text)
    return 1 if findings.items else 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:3]))
