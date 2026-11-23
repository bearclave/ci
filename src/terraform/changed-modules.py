#!/usr/bin/env python3
"""Lists the Terraform modules a run has to check.

Usage: changed-modules.py <outdir> [<base commit>]

Writes validate.txt, test.txt and readme.txt to <outdir>, one module directory
per line: the directories that hold .tf files, those with tests/*.tftest.hcl,
and those with a README.md (the terraform-docs tables).

Without a base commit (a push, a schedule, an unknown base) every module is
listed. With one - a pull request - only the modules the change touches: a
module is a directory with .tf files, a file belongs to the nearest module
above it, and a module that calls a changed one through a relative `source`
is listed too. The whole set is listed when the change reaches anything that
every module shares: a path under a `_common` folder, the workflows, the
tool versions, the lint and docs settings, any file outside every module
(Markdown, .claude/ and docs/ excepted, which no check of a module reads).
"""
import os
import re
import subprocess
import sys

# Outside every module, these cannot change what a module check says.
HARMLESS = (".claude/", "docs/")
RE_SOURCE = re.compile(r'^\s*source\s*=\s*"(\.\.?/[^"]*)"', re.M)


def git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout


def lists(files: list) -> dict:
    """{validate, test, readme: sorted module directories} of all modules."""
    tf = {os.path.dirname(f) or "." for f in files if f.endswith(".tf") and "/.terraform/" not in f
          and not f.startswith(".tools/")}
    tests = {os.path.dirname(os.path.dirname(f)) or "." for f in files
             if f.endswith(".tftest.hcl") and os.path.basename(os.path.dirname(f)) == "tests"}
    readme = {d for d in tf if (f"{d}/README.md" if d != "." else "README.md") in files
              and not any(p.startswith(".") and p != "." for p in d.split("/"))}
    return {"validate": sorted(tf), "test": sorted(tests), "readme": sorted(readme)}


def owner(path: str, modules: set):
    d = os.path.dirname(path)
    while True:
        if (d or ".") in modules:
            return d or "."
        if not d:
            return None
        d = os.path.dirname(d)


def dependents(changed: set, modules: set, read) -> set:
    """The modules that call a module of `changed` by a relative source, transitively."""
    out = set(changed)
    grew = True
    while grew:
        grew = False
        for m in modules - out:
            for src in RE_SOURCE.findall(read(m)):
                target = os.path.normpath(os.path.join(m, src))
                if any(target == c or target.startswith(c + "/") for c in out):
                    out.add(m)
                    grew = True
                    break
    return out


def select(files: list, changes, read=None):
    """(lists, reason): the lists to check, and why they are whole when they are (else None)."""
    everything = lists(files)
    if changes is None:
        return everything, "no base to compare with"
    modules = set(everything["validate"])
    read = read or (lambda m: "".join(open(os.path.join(m, f), encoding="utf-8").read()
                                      for f in os.listdir(m) if f.endswith(".tf")))
    touched = set()
    for path in changes:
        if "_common" in path.split("/"):
            return everything, f"{path} is shared by every module"
        mod = owner(path, modules)
        if mod:
            touched.add(mod)
        elif path.startswith(HARMLESS) or path.lower().endswith(".md"):
            continue
        else:
            return everything, f"{path} is outside every module"
    chosen = dependents(touched, modules, read)
    return {k: [d for d in v if d in chosen] for k, v in everything.items()}, None


def main(argv: list) -> int:
    if not argv:
        sys.exit(__doc__)
    outdir, base = argv[0], argv[1] if len(argv) > 1 else ""
    files = git("ls-files", "-z").split("\0")
    files = [f for f in files if f]
    changes = None
    if base:
        try:
            changes = [f for f in git("diff", "--name-only", "-z", base, "HEAD").split("\0") if f]
        except subprocess.CalledProcessError:
            changes = None
    chosen, reason = select(files, changes)
    os.makedirs(outdir, exist_ok=True)
    for name, dirs in chosen.items():
        with open(os.path.join(outdir, f"{name}.txt"), "w", encoding="utf-8") as fh:
            fh.write("".join(d + "\n" for d in dirs))
    total = len(lists(files)["validate"])
    line = (f"Modules checked: all {total} ({reason})." if reason
            else f"Modules checked: {len(chosen['validate'])} of {total}.")
    print(line)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
