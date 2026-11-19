#!/usr/bin/env python3
"""Checks the root layout of a repository against governance standards/layout.yaml.

Usage: layout_check.py <layout.json> <exceptions.json> <repository name> [<root>]
       layout_check.py --self-test
"""
import datetime
import fnmatch
import json
import os
import re
import subprocess
import sys
import tempfile

PIPELINES = ("terragrunt-home", "terragrunt", "terraform-modules", "m365-dsc",
             "static-web-app", "container", "content-sentinel", "checks")


def group_of(name: str) -> str:
    for prefix, group in (("iac-", "iac"), ("app-", "app"), ("template-", "template"), ("content-", "template")):
        if name.startswith(prefix):
            return group
    return "platform"


def pipeline_of(name: str, root: str) -> str:
    """The shared workflow the repository's ci.yml calls; ci runs its own."""
    if name == "ci":
        return "ci"
    try:
        with open(os.path.join(root, ".github", "workflows", "ci.yml"), encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return ""
    for m in re.finditer(r"uses:\s*\S*?\.github/workflows/([\w-]+)\.yml", text):
        if m.group(1) in PIPELINES:
            return m.group(1)
    return ""


def tracked(root: str) -> list:
    out = subprocess.run(["git", "-C", root, "ls-files"], capture_output=True, text=True, check=True).stdout
    return out.splitlines()


def merged(layout: dict, group: str, pipeline: str, key: str) -> set:
    parts = [layout.get("common") or {}, (layout.get("groups") or {}).get(group) or {},
             (layout.get("pipelines") or {}).get(pipeline) or {}]
    return {x for p in parts for x in (p.get(key) or [])}


def content_kind(layout: dict, group: str, pipeline: str) -> str:
    return ((layout.get("pipelines") or {}).get(pipeline) or {}).get("content", "")


def holds(files: list, folder: str, kind: str) -> bool:
    inside = [f for f in files if f.startswith(folder + "/")]
    if kind == "units":
        return any(f.rsplit("/", 1)[-1] == "terragrunt.hcl" for f in inside)
    if kind == "modules":
        return any(f.endswith(".tf") for f in inside)
    return False


def check(layout: dict, config: dict, name: str, root: str) -> tuple:
    """(errors as (path, message), warnings)."""
    group, pipeline = group_of(name), pipeline_of(name, root)
    errors, warnings = [], []
    if not pipeline:
        return [(".github/workflows/ci.yml", "no shared pipeline of bearclave/ci found in ci.yml")], []
    exceptions = config.get("exceptions") or []
    for k, e in enumerate(exceptions):
        if not str(e.get("path", "")).strip() or not str(e.get("reason", "")).strip():
            errors.append((".github/conventions.yaml", f"layout exception {k + 1} needs a path and a reason"))
    used = set()

    def excepted(path: str) -> bool:
        for k, e in enumerate(exceptions):
            if e.get("path") and e.get("reason") and fnmatch.fnmatchcase(path, str(e["path"])):
                used.add(k)
                return True
        return False

    files = tracked(root)
    root_files = {f for f in files if "/" not in f}
    root_dirs = {f.split("/", 1)[0] for f in files if "/" in f}
    allowed_files = merged(layout, group, pipeline, "files")
    allowed_dirs = merged(layout, group, pipeline, "folders")
    kind = content_kind(layout, group, pipeline)

    for entry in sorted(merged(layout, group, pipeline, "required")):
        if entry not in root_files | root_dirs and not excepted(entry):
            errors.append((entry, f"{entry} is required in the root"))
    for entry in sorted(root_files):
        if entry not in allowed_files and not excepted(entry):
            errors.append((entry, f"{entry} does not belong in the root (layout.md: .github/, tools/, docs/ or a folder)"))
    for entry in sorted(root_dirs):
        if entry in allowed_dirs or (kind and holds(files, entry, kind)) or excepted(entry):
            continue
        errors.append((entry, f"{entry}/ is not a folder of the layout for {group}/{pipeline}"))

    allowed_github = merged(layout, group, pipeline, "github")
    for entry in sorted({f.split("/")[1] for f in files if f.startswith(".github/")}):
        if entry not in allowed_github and not excepted(f".github/{entry}"):
            errors.append((f".github/{entry}", f".github/{entry} does not belong in .github/ (GitHub and CI only)"))

    docs_name = (layout.get("common") or {}).get("docs_name")
    if docs_name:
        for entry in sorted({f.split("/")[1] for f in files if f.startswith("docs/")}):
            if not re.match(docs_name, entry) and not excepted(f"docs/{entry}"):
                errors.append((f"docs/{entry}", f"docs/{entry} is not a kebab-case name"))

    for k, e in enumerate(exceptions):
        if k not in used and e.get("path") and e.get("reason"):
            warnings.append((".github/conventions.yaml", f"layout exception for {e['path']} matches nothing"))
        # review (YYYY-MM-DD) is optional here: a layout exception is
        # structure. A date that has passed only warns.
        review = str(e.get("review") or "")
        if review and not re.match(r"^\d{4}-\d{2}-\d{2}$", review):
            errors.append((".github/conventions.yaml", f"layout exception for {e.get('path')}: review {review!r} is not YYYY-MM-DD"))
        elif review and review < datetime.date.today().isoformat():
            warnings.append((".github/conventions.yaml",
                             f"layout exception for {e.get('path')} was due for review on {review}"))
    return errors, warnings


def main(layout_path: str, config_path: str, name: str, root: str = ".") -> int:
    with open(layout_path, encoding="utf-8") as fh:
        layout = json.load(fh) or {}
    with open(config_path, encoding="utf-8") as fh:
        config = json.load(fh) or {}
    errors, warnings = check(layout, config, name, root)
    for path, message in errors:
        print(f"::error file={path},title=layout::{message}")
    for path, message in warnings:
        print(f"::warning file={path},title=layout::{message}")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write("## Layout\n\n")
            if not errors and not warnings:
                fh.write(f"The root follows the layout ({group_of(name)}/{pipeline_of(name, root)}).\n")
            for path, message in errors + warnings:
                fh.write(f"- `{path}`: {message}\n")
    return 1 if errors else 0


def self_test() -> int:
    layout = {"common": {"required": ["README.md", ".github"], "files": ["README.md", ".gitignore"],
                         "folders": [".github", "src", "docs"], "github": ["workflows"],
                         "docs_name": r"^[a-z0-9]+(-[a-z0-9]+)*(\.[a-z0-9]+)?$"},
              "groups": {"app": {"folders": ["content"]}},
              "pipelines": {"terragrunt": {"files": ["root.hcl"], "content": "units"}, "checks": {}}}
    cases = [
        ("iac-x", "terragrunt", ["README.md", "root.hcl", "mg/rg/terragrunt.hcl", "docs/setup.md"], {}, 0),
        ("iac-x", "terragrunt", ["README.md", "SETUP.md", "scripts/a.sh", "docs/Setup.md"], {}, 3),
        ("app-x", "checks", ["README.md", "content/a.txt", ".github/scripts/a.sh"], {}, 1),
        ("handbook", "checks", ["README.md", "gotchas/a.md"],
         {"exceptions": [{"path": "gotchas", "reason": "content"}]}, 0),
        ("handbook", "checks", ["gotchas/a.md"], {"exceptions": [{"path": "gotchas"}]}, 3),
    ]
    failed = 0
    for name, pipeline, paths, config, expected in cases:
        with tempfile.TemporaryDirectory() as root:
            ci = ".github/workflows/ci.yml"
            for path in paths + [ci]:
                os.makedirs(os.path.join(root, os.path.dirname(path)), exist_ok=True)
                with open(os.path.join(root, path), "w", encoding="utf-8") as fh:
                    fh.write(f"uses: bearclave/ci/.github/workflows/{pipeline}.yml@v2\n" if path == ci else "")
            subprocess.run(["git", "init", "-q", root], check=True)
            subprocess.run(["git", "-C", root, "add", "-A"], check=True)
            errors, _ = check(layout, config, name, root)
            if len(errors) != expected:
                failed += 1
                print(f"FAIL {name} {paths}: {len(errors)} errors, expected {expected}: {errors}")
    print("self-test failed" if failed else "self-test passed")
    return 1 if failed else 0


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        sys.exit(self_test())
    sys.exit(main(*sys.argv[1:]))
