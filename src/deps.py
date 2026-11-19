#!/usr/bin/env python3
"""Keeps the pinned tools, Terraform providers and actions current.

Run by .github/workflows/deps.yml as the GitHub App bearclave-bot (see
github_bot.py). Dependabot reads none of these pins, or (the actions of
this repository) bumps the generated workflows instead of templates/:

  tools      NAME_VERSION / NAME_SHA256 in .github/tool-versions.env of every
             repository, and the security tools in templates/security.yml
  providers  the exact versions in `versions = { ... }` of root.hcl or
             account.hcl (Terragrunt repositories); module lower bounds
             (`>= 5.0`) are only reported
  actions    `uses: owner/repo@<sha> # vX.Y.Z` in templates/ and the
             workflows of this repository that are not generated; then
             tools/build.py writes the generated ones

A new version is proposed when it is a release (no pre-release), at least
COOLDOWN_DAYS old - a compromised release is usually pulled within days -
and of the same major version. A newer major is only listed in the pull
request: it is its own pull request, by hand, with the upgrade guide. The
sha256 of a tool comes from the release asset's `digest`, or from
downloading the asset when GitHub has none.

One pull request per repository and scope, from branch deps/<scope>,
assigned to the owner and labelled type:deps; the owner approves and
merges. A pull request still open on that branch is left alone: nothing
new is proposed there until it is merged or closed.

Usage: deps.py [--scope all|tools|actions] [--dry-run] [--repo NAME ...]

  --scope tools    tools and providers (monthly)
  --scope actions  actions of this repository (weekly)
  --dry-run        read only and print the changes as diffs; reads with
                   GH_TOKEN when it is set, else as the bot
  --repo NAME      only these repositories (repeatable)
"""
import datetime
import difflib
import hashlib
import os
import pathlib
import re
import subprocess
import sys
import urllib.parse
import xml.etree.ElementTree as ET

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import github_bot as gh  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
THIS_REPO = "ci"
COOLDOWN_DAYS = 7
# iac-m365 is being retired.
SKIP = {"iac-m365"}

GH_DL = "https://github.com"
# NAME of NAME_VERSION -> where its releases are, and the asset whose sha256
# is pinned ({v}: the version without the v). No asset: only the version.
TOOLS = {
    "TERRAFORM": {"github": "hashicorp/terraform"},
    "TERRAGRUNT": {"github": "gruntwork-io/terragrunt", "asset": "terragrunt_linux_amd64"},
    "TRIVY": {"github": "aquasecurity/trivy", "asset": "trivy_{v}_Linux-64bit.tar.gz"},
    "TFLINT": {"github": "terraform-linters/tflint", "asset": "tflint_linux_amd64.zip"},
    "GITLEAKS": {"github": "gitleaks/gitleaks", "asset": "gitleaks_{v}_linux_x64.tar.gz"},
    "ACTIONLINT": {"github": "rhysd/actionlint", "asset": "actionlint_{v}_linux_amd64.tar.gz"},
    "ZIZMOR": {"github": "zizmorcore/zizmor", "asset": "zizmor-x86_64-unknown-linux-gnu.tar.gz"},
    "SHELLCHECK": {"github": "koalaman/shellcheck", "asset": "shellcheck-v{v}.linux.x86_64.tar.gz"},
    "M365DSC": {"psgallery": "Microsoft365DSC"},
}
# Providers whose source is not in _common/templates/*.tmpl (a unit
# generates it itself).
PROVIDER_SOURCES = {"msgraph": "microsoft/msgraph"}

TOOL_FILES = (".github/tool-versions.env",)
PROVIDER_FILES = ("root.hcl", "account.hcl")
SOURCE_FILES = ("_common/templates/versions.tf.tmpl",)
CI_TOOL_FILES = ("templates/security.yml",)


# --- versions ------------------------------------------------------------------

STABLE = re.compile(r"^v?(\d+(?:\.\d+)+)$")


def parse(version: str):
    """(1, 2, 3) of a release version, else None (pre-releases, other tags)."""
    m = STABLE.match(version.strip())
    return tuple(int(x) for x in m.group(1).split(".")) if m else None


def when(text: str) -> datetime.datetime:
    text = text.strip().replace("Z", "+00:00")
    text = re.sub(r"\.(\d{6})\d+", r".\1", text)  # PSGallery: 7 decimal places
    d = datetime.datetime.fromisoformat(text)
    return d if d.tzinfo else d.replace(tzinfo=datetime.timezone.utc)


def pick(current: str, releases: list, now: datetime.datetime, cooldown: int = COOLDOWN_DAYS):
    """(newest of the same major, newest of a newer major) - each a release
    dict or None - among releases ({"version", "published"} at least) that
    are newer than current and at least cooldown days old."""
    cur = parse(current)
    if cur is None:
        return None, None
    ready = [r for r in releases
             if parse(r["version"]) and r.get("published")
             and now - r["published"] >= datetime.timedelta(days=cooldown)
             and parse(r["version"]) > cur]
    same = [r for r in ready if parse(r["version"])[0] == cur[0]]
    major = [r for r in ready if parse(r["version"])[0] > cur[0]]
    top = lambda rs: max(rs, key=lambda r: parse(r["version"])) if rs else None  # noqa: E731
    return top(same), top(major)


# --- the pins in a file (pure, see tests/test_deps.py) ---------------------------

def tool_pins(text: str) -> dict:
    """{NAME: version} of NAME_VERSION=x (env) or NAME_VERSION: "x" (YAML)."""
    found = {}
    for m in re.finditer(r'^\s*([A-Z0-9]+)_VERSION\s*[=:]\s*"?([^"\s#]+)"?\s*(?:#.*)?$', text, re.M):
        found[m.group(1)] = m.group(2)
    return found


def set_value(text: str, key: str, value: str) -> str:
    """text with the value of KEY=... or KEY: ... replaced, quotes kept."""
    pattern = re.compile(r'^(\s*' + re.escape(key) + r'\s*[=:]\s*"?)([^"\s#]+)', re.M)
    new, n = pattern.subn(lambda m: m.group(1) + value, text)
    if n == 0:
        raise KeyError(key)
    return new


def set_tool(text: str, name: str, version: str, sha256=None) -> str:
    text = set_value(text, f"{name}_VERSION", version)
    if sha256 is not None:
        text = set_value(text, f"{name}_SHA256", sha256)
    return text


VERSIONS_BLOCK = re.compile(r"^([ \t]*)versions\s*=\s*\{(.*?)^\1\}", re.M | re.S)
EXACT = re.compile(r'^\s*([a-z0-9_-]+)\s*=\s*"(\d+\.\d+\.\d+)"', re.M)


def provider_pins(text: str) -> dict:
    """{name: version} of the exact versions in a `versions = { ... }` block."""
    m = VERSIONS_BLOCK.search(text)
    return dict(EXACT.findall(m.group(2))) if m else {}


def set_provider(text: str, name: str, version: str) -> str:
    m = VERSIONS_BLOCK.search(text)
    if not m:
        raise KeyError(name)
    block = m.group(2)
    pattern = re.compile(r'^(\s*' + re.escape(name) + r'\s*=\s*")(\d+\.\d+\.\d+)(")', re.M)
    new_block, n = pattern.subn(lambda x: x.group(1) + version + x.group(3), block)
    if n != 1:
        raise KeyError(name)
    return text[:m.start(2)] + new_block + text[m.end(2):]


SOURCE = re.compile(r'([a-z0-9_-]+)\s*=\s*\{\s*source\s*=\s*"([^"]+)"', re.S)
BOUND = re.compile(r'([a-z0-9_-]+)\s*=\s*\{\s*source\s*=\s*"([^"]+)"\s*,?\s*version\s*=\s*"([^"]+)"', re.S)


def provider_sources(text: str) -> dict:
    return {name: source for name, source in SOURCE.findall(text)}


def module_bounds(text: str) -> list:
    """[(name, source, constraint)] of required_providers in a module."""
    return BOUND.findall(text)


ACTION = re.compile(r"(uses:\s*)([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)((?:/[^@\s]*)?)@([0-9a-f]{40})(\s*#\s*)(v?[0-9][\w.-]*)")


def action_pins(text: str) -> set:
    """{(owner/repo, sha, version tag)} of the actions pinned to a commit."""
    return {(m.group(2), m.group(4), m.group(6)) for m in ACTION.finditer(text)
            if not m.group(2).lower().startswith("bearclave/")}


def set_action(text: str, repo: str, old_sha: str, sha: str, tag: str) -> str:
    def repl(m):
        if m.group(2).lower() != repo.lower() or m.group(4) != old_sha:
            return m.group(0)
        return f"{m.group(1)}{m.group(2)}{m.group(3)}@{sha}{m.group(5)}{tag}"
    return ACTION.sub(repl, text)


# --- upstream --------------------------------------------------------------------

class Upstream:
    """Releases of tools, providers and actions, each looked up once."""

    def __init__(self, token: str):
        self.token = token
        self.cache = {}

    def _once(self, key, fn):
        if key not in self.cache:
            self.cache[key] = fn()
        return self.cache[key]

    def github(self, repo: str) -> list:
        def fetch():
            out = []
            for r in gh.request("GET", f"/repos/{repo}/releases?per_page=100", self.token):
                if r.get("draft") or r.get("prerelease") or not r.get("published_at"):
                    continue
                out.append({"version": r["tag_name"].lstrip("v"), "tag": r["tag_name"],
                            "published": when(r["published_at"]), "assets": r.get("assets", []),
                            "url": r.get("html_url")})
            return out
        return self._once(("github", repo), fetch)

    def psgallery(self, module: str) -> list:
        def fetch():
            url = ("https://www.powershellgallery.com/api/v2/FindPackagesById()?id="
                   + urllib.parse.quote(f"'{module}'") + "&$orderby=Published%20desc&$top=100")
            ns = {"a": "http://www.w3.org/2005/Atom",
                  "m": "http://schemas.microsoft.com/ado/2007/08/dataservices/metadata",
                  "d": "http://schemas.microsoft.com/ado/2007/08/dataservices"}
            root = ET.fromstring(gh.request("GET", url, raw=True))
            out = []
            for entry in root.findall("a:entry", ns):
                props = entry.find("m:properties", ns)
                version = props.findtext("d:Version", default="", namespaces=ns)
                if props.findtext("d:IsPrerelease", default="false", namespaces=ns) == "true":
                    continue
                published = props.findtext("d:Published", default="", namespaces=ns)
                if version and published:
                    out.append({"version": version, "published": when(published),
                                "url": f"https://www.powershellgallery.com/packages/{module}/{version}"})
            return out
        return self._once(("psgallery", module), fetch)

    def provider(self, source: str) -> list:
        def fetch():
            ns, name = source.lower().split("/")
            body = gh.request("GET", f"https://registry.terraform.io/v2/providers/{ns}/{name}?include=provider-versions")
            return [{"version": i["attributes"]["version"], "published": when(i["attributes"]["published-at"]),
                     "url": f"https://registry.terraform.io/providers/{ns}/{name}/{i['attributes']['version']}"}
                    for i in body.get("included", []) if i.get("type") == "provider-versions"]
        return self._once(("provider", source.lower()), fetch)

    def tool(self, name: str) -> list:
        spec = TOOLS[name]
        return self.github(spec["github"]) if "github" in spec else self.psgallery(spec["psgallery"])

    def sha256(self, name: str, release: dict):
        """sha256 of the tool's pinned asset in a release; None for a tool
        that pins no checksum."""
        asset_name = TOOLS[name].get("asset")
        if not asset_name:
            return None
        want = asset_name.format(v=release["version"])
        for asset in release.get("assets", []):
            if asset["name"] != want:
                continue
            digest = asset.get("digest") or ""
            if digest.startswith("sha256:"):
                return digest.split(":", 1)[1]
            data = gh.request("GET", asset["browser_download_url"], raw=True)
            return hashlib.sha256(data).hexdigest()
        raise LookupError(f"{name} {release['version']}: no asset {want}")

    def commit(self, repo: str, tag: str) -> str:
        def fetch():
            obj = gh.request("GET", f"/repos/{repo}/git/ref/tags/{tag}", self.token)["object"]
            while obj["type"] == "tag":
                obj = gh.request("GET", f"/repos/{repo}/git/tags/{obj['sha']}", self.token)["object"]
            return obj["sha"]
        return self._once(("commit", repo, tag), fetch)


# --- what changes in a repository -------------------------------------------------

class Plan:
    """The new text of the files of one repository and what changed."""

    def __init__(self, repo: str):
        self.repo = repo
        self.files = {}      # path -> new text
        self.changes = []    # (what, old, new, link)
        self.majors = []     # (what, current, newer major, link)
        self.notes = []

    def edit(self, path: str, original: str, fn):
        self.files[path] = fn(self.files.get(path, original))

    def changed(self, originals: dict) -> dict:
        return {p: t for p, t in self.files.items() if t != originals.get(p)}


def plan_tools(plan: Plan, path: str, text: str, up: Upstream, now) -> None:
    for name, current in sorted(tool_pins(text).items()):
        if name not in TOOLS:
            plan.notes.append(f"`{name}` in `{path}`: not a known tool, not checked")
            continue
        try:
            same, major = pick(current, up.tool(name), now)
            if major:
                plan.majors.append((f"{name} (`{path}`)", current, major["version"], major.get("url")))
            if same:
                sha = up.sha256(name, same)
                plan.edit(path, text, lambda t, n=name, v=same["version"], s=sha: set_tool(t, n, v, s))
                plan.changes.append((f"{name} (`{path}`)", current, same["version"], same.get("url")))
        except (gh.ApiError, gh.NotFound, LookupError, KeyError) as e:
            plan.notes.append(f"`{name}` in `{path}`: not checked ({e})")


def plan_providers(plan: Plan, path: str, text: str, sources: dict, up: Upstream, now) -> None:
    for name, current in sorted(provider_pins(text).items()):
        source = sources.get(name) or PROVIDER_SOURCES.get(name)
        if not source:
            continue  # not a provider (e.g. routeros_os)
        try:
            same, major = pick(current, up.provider(source), now)
            if major:
                plan.majors.append((f"{source} (`{path}`)", current, major["version"], major.get("url")))
            if same:
                plan.edit(path, text, lambda t, n=name, v=same["version"]: set_provider(t, n, v))
                plan.changes.append((f"{source} (`{path}`)", current, same["version"], same.get("url")))
        except (gh.ApiError, gh.NotFound, KeyError) as e:
            plan.notes.append(f"`{source}` in `{path}`: not checked ({e})")


def plan_actions(plan: Plan, texts: dict, up: Upstream, now) -> None:
    pins = {}
    for path, text in texts.items():
        for repo, sha, tag in action_pins(text):
            pins.setdefault((repo, sha, tag), []).append(path)
    for (repo, sha, tag), paths in sorted(pins.items()):
        try:
            same, major = pick(tag, up.github(repo), now)
            if major:
                plan.majors.append((repo, tag, major["tag"], major.get("url")))
            if not same:
                continue
            new_sha = up.commit(repo, same["tag"])
            for path in paths:
                plan.edit(path, texts[path], lambda t, r=repo, o=sha, n=new_sha, g=same["tag"]: set_action(t, r, o, n, g))
            plan.changes.append((repo, tag, same["tag"], same.get("url")))
        except (gh.ApiError, gh.NotFound) as e:
            plan.notes.append(f"`{repo}`: not checked ({e})")


# --- repositories -------------------------------------------------------------------

def remote_reader(repo: str, ref: str, token: str):
    cache = {}

    def read(path: str):
        if path not in cache:
            cache[path] = gh.file_text(f"{gh.ORG}/{repo}", path, ref, token)
        return cache[path]
    return read


def local_reader(root: pathlib.Path):
    def read(path: str):
        p = root / path
        return p.read_text(encoding="utf-8") if p.is_file() else None
    return read


def own_workflows(root: pathlib.Path) -> list:
    """templates/ and the workflows of this repository build.py does not write."""
    paths = sorted(str(p.relative_to(root)) for p in (root / "templates").rglob("*.yml"))
    for p in sorted((root / ".github" / "workflows").glob("*.yml")):
        if not p.read_text(encoding="utf-8").startswith("# GENERATED by tools/build.py"):
            paths.append(str(p.relative_to(root)))
    return paths


def plan_repo(repo: str, read, scope: str, up: Upstream, now, list_files=None) -> tuple:
    """(Plan, original texts) of one repository."""
    plan, originals = Plan(repo), {}

    def load(path):
        text = read(path)
        if text is not None:
            originals[path] = text
        return text

    if scope == "tools":
        for path in TOOL_FILES + (CI_TOOL_FILES if repo == THIS_REPO else ()):
            text = load(path)
            if text:
                plan_tools(plan, path, text, up, now)
        sources = {}
        for path in SOURCE_FILES:
            sources.update(provider_sources(read(path) or ""))
        for path in PROVIDER_FILES:
            text = load(path)
            if text:
                plan_providers(plan, path, text, sources, up, now)
    elif scope == "actions" and repo == THIS_REPO and list_files:
        texts = {p: load(p) for p in list_files()}
        plan_actions(plan, {p: t for p, t in texts.items() if t}, up, now)
    return plan, originals


def bounds_report(repo: str, token: str, ref: str, up: Upstream, now) -> list:
    """Lines on the provider lower bounds of a module repository."""
    tree = gh.request("GET", f"/repos/{gh.ORG}/{repo}/git/trees/{ref}?recursive=1", token)
    seen = {}
    for item in tree.get("tree", []):
        if item["type"] == "blob" and item["path"].endswith("versions.tf"):
            text = gh.file_text(f"{gh.ORG}/{repo}", item["path"], ref, token) or ""
            for _, source, constraint in module_bounds(text):
                seen.setdefault((source.lower(), constraint), 0)
                seen[(source.lower(), constraint)] += 1
    lines = []
    for (source, constraint), count in sorted(seen.items()):
        try:
            latest = max((r for r in up.provider(source) if parse(r["version"])), key=lambda r: parse(r["version"]))
        except (gh.ApiError, ValueError):
            continue
        lines.append(f"| {repo} | {source} | `{constraint}` ({count}x) | {latest['version']} |")
    return lines


# --- the pull request ---------------------------------------------------------------

TITLES = {"tools": "deps: bump pinned tools and terraform providers",
          "actions": "deps: bump the actions of the shared workflows"}


def link(text: str, url) -> str:
    return f"[{text}]({url})" if url else text


def body(plan: Plan, scope: str, generated: list) -> str:
    rows = "\n".join(f"| {what} | {old} | {link(new, url)} |" for what, old, new, url in plan.changes)
    majors = "\n".join(f"- {what}: {old} -> {link(new, url)}" for what, old, new, url in plan.majors) or "none"
    notes = "\n".join(f"- {n}" for n in plan.notes)
    why = ("Monthly check of the pinned tools and Terraform providers against their upstream releases "
           if scope == "tools" else
           "Weekly check of the actions pinned in the shared workflows against their releases ")
    production = ("yes: the plan of this pull request shows what the new versions change"
                  if scope == "tools" and any(p in plan.files for p in PROVIDER_FILES + TOOL_FILES) else "no")
    parts = [
        "## Why", "",
        why + f"(`deps.yml` in `bearclave/ci`). Only releases at least {COOLDOWN_DAYS} days old, "
        "within the same major version.", "",
        "## What changes", "",
        "| What | From | To |", "|---|---|---|", rows, "",
        "Newer major versions, not proposed (a pull request of their own, with the upgrade guide):", "", majors, "",
    ]
    if generated:
        parts += ["Regenerated by `tools/build.py`: " + ", ".join(f"`{g}`" for g in generated), ""]
    if notes:
        parts += ["Not checked:", "", notes, ""]
    parts += [
        "## Impact", "",
        f"- Production: {production}",
        "- Destroyed or replaced: see the plan" if production != "no" else "- Destroyed or replaced: none",
        "- Manual steps after merge: none", "",
        "## Verification", "",
        "The checksums are the `digest` GitHub publishes for each release asset (or the downloaded asset's own). "
        "The pipeline of this pull request runs with the new versions.", "",
        "## Review", "",
        "Read the release notes linked above. Opened by `bearclave-bot`; the owner approves and merges.", "",
        "## Links", "",
        "bearclave/governance standards/toolchain.md, standards/security.md#supply-chain", "",
    ]
    return "\n".join(parts)


def commit_message(plan: Plan, scope: str) -> str:
    lines = [f"- {what}: {old} -> {new}" for what, old, new, _ in plan.changes]
    return TITLES[scope] + "\n\n" + "\n".join(lines).replace("`", "") + "\n"


def regenerate(root: pathlib.Path, plan: Plan, originals: dict) -> list:
    """Write the edited templates, run tools/build.py and add what it changed."""
    for path, text in plan.files.items():
        (root / path).write_text(text, encoding="utf-8")
    try:
        subprocess.run([sys.executable, str(root / "tools" / "build.py")], cwd=root, check=True)
        out = subprocess.run(["git", "status", "--porcelain", "--", ".github/workflows"], cwd=root,
                             capture_output=True, text=True, check=True).stdout
        generated = sorted(line[3:] for line in out.splitlines())
        for path in generated:
            originals.setdefault(path, subprocess.run(["git", "show", f"HEAD:{path}"], cwd=root,
                                                      capture_output=True, text=True).stdout)
            plan.files[path] = (root / path).read_text(encoding="utf-8")
        return generated
    finally:
        subprocess.run(["git", "checkout", "--", "templates", ".github/workflows"], cwd=root, check=False)


def main(argv: list) -> int:
    dry_run = "--dry-run" in argv
    scope, only = "all", []
    args = [a for a in argv if a != "--dry-run"]
    while args:
        a = args.pop(0)
        if a == "--scope" and args:
            scope = args.pop(0)
        elif a == "--repo" and args:
            only.append(args.pop(0))
        else:
            sys.exit(__doc__)
    if scope not in ("all", "tools", "actions"):
        sys.exit(__doc__)
    scopes = ["tools", "actions"] if scope == "all" else [scope]

    bot = None
    if dry_run and os.environ.get("GH_TOKEN"):
        read_token = os.environ["GH_TOKEN"]
    else:
        bot = gh.Bot()
        read_token = bot.token(gh.READ)
    up = Upstream(read_token)
    now = datetime.datetime.now(datetime.timezone.utc)

    repos = [r for r in gh.repositories(read_token) if r["name"] not in SKIP and not r.get("fork")]
    if only:
        repos = [r for r in repos if r["name"] in only]

    gh.say(f"## Dependencies ({', '.join(scopes)}{', dry run' if dry_run else ''})")
    gh.say()
    opened, bounds = [], []
    for scope_ in scopes:
        for r in repos:
            name, base = r["name"], r["default_branch"]
            if scope_ == "actions" and name != THIS_REPO:
                continue
            if name == THIS_REPO:
                read, list_files = local_reader(ROOT), (lambda: own_workflows(ROOT))
                base_sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                                          text=True, check=True).stdout.strip()
            else:
                base_sha = gh.request("GET", f"/repos/{gh.ORG}/{name}/commits/{base}", read_token)["sha"]
                read, list_files = remote_reader(name, base_sha, read_token), None
            plan, originals = plan_repo(name, read, scope_, up, now, list_files)
            if scope_ == "tools" and name.endswith("-modules"):
                bounds += bounds_report(name, read_token, base_sha, up, now)
            generated = []
            if name == THIS_REPO and plan.changed(originals) and any(p.startswith("templates/") for p in plan.files):
                generated = regenerate(ROOT, plan, originals)
            changed = plan.changed(originals)
            for what, old, new, _ in plan.changes:
                gh.say(f"- {name}: {what} {old} -> {new}")
            for what, old, new, _ in plan.majors:
                gh.say(f"- {name}: {what} {old}, new major {new} not proposed")
            for note in plan.notes:
                gh.say(f"- {name}: {note}")
            if not changed:
                continue
            branch = f"deps/{scope_}"
            if dry_run:
                for path, text in sorted(changed.items()):
                    sys.stdout.writelines(difflib.unified_diff(
                        originals.get(path, "").splitlines(True), text.splitlines(True),
                        f"a/{name}/{path}", f"b/{name}/{path}"))
                gh.say(f"- {name}: would open `{TITLES[scope_]}` from `{branch}` ({len(changed)} files)")
                continue
            token = bot.token(gh.WRITE, [name])
            existing = gh.open_pull(f"{gh.ORG}/{name}", branch, token)
            if existing:
                gh.say(f"- {name}: #{existing['number']} is still open on `{branch}`; merge or close it first")
                continue
            gh.commit_files(f"{gh.ORG}/{name}", token, base_sha, branch, changed, commit_message(plan, scope_))
            pr = gh.create_pull(f"{gh.ORG}/{name}", token, branch, base, TITLES[scope_], body(plan, scope_, generated))
            opened.append(pr["html_url"])
            gh.say(f"- {name}: opened {pr['html_url']}")
    if bounds:
        gh.say()
        gh.say("### Module lower bounds (information only)")
        gh.say()
        gh.say("| Repository | Provider | Constraint | Latest |")
        gh.say("|---|---|---|---|")
        for line in bounds:
            gh.say(line)
    gh.say()
    gh.say(f"Pull requests opened: {len(opened)}" if not dry_run else "Dry run: nothing written.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
