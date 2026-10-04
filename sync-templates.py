#!/usr/bin/env python3
"""Brings the template repositories up to their live repositories.

Usage: sync-templates.py [--check] [<repositories root>] [<template>[=<checkout>] ...]

A template may name the checkout to write to (a worktree, say); otherwise it
is <repositories root>/<template>.

The templates (template-iac-*) are what a customer copies; the live
repositories (iac-*) are where the code is developed and proven. Every
template takes from its live repository what is engine - the pipeline, the
scripts and their tests, the runner, the modules - and keeps what is the
tenant: account.hcl, the units, the configuration of the tenant, its README.
Without this the templates fell behind within weeks.

For each template below: `copy` lists what is taken over (directories whole,
removing what live no longer has), `keep` what stays the template's even
inside a copied directory, `replace` the organisation's values and their
placeholders, `edits` the exact changes a template needs on top (the jobs that
sign in to a tenant wait until the customer fills in the variables). An edit
whose text is no longer found fails loudly: the live file changed shape and
the rule has to be looked at. The copies of the shared workflows of this
repository are written by export-for-templates.py, which this runs at the end.

Nothing that names the organisation or its tenant may reach a template; such
a file stops the run. --check writes nothing and lists what is out of date.
The repositories root defaults to the directory above this repository.
"""
import filecmp
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent
FORBIDDEN = re.compile(
    r"bearclave|matej|hrabalek|31a3b839-94d3-44da-ab17-0dff1ac2bd1e|1827efe4-672d-413a-9317-4d9298871317|"
    r"ae91cfad-f27a-43a1-b054-d283b0c13028|doklady|sp-swa-(?:sro|osvc)|kv-sro|-sro-weu|-osvc-weu",
    re.I)
SHARED_CALL = (r"uses: bearclave/ci/\.github/workflows/([\w-]+\.yml)@v1", r"uses: ./.github/workflows/\1")

TERRAGRUNT_GUARD = """  # Everything from here on signs in to Azure. Until AZURE_CLIENT_ID_PLATFORM
  # is filled in the jobs are skipped, so a freshly created repository is green
  # and only the checks that need no tenant run.
  plan:"""

TEMPLATES = {
    "template-iac-terragrunt": {
        "live": "iac-terragrunt",
        "copy": [".github/scripts", ".github/workflows/terragrunt.yml", ".github/workflows/drift.yml",
                 ".github/workflows/scripts.yml", ".github/tool-versions.env"],
        "keep": [],
        "replace": [("bearclave/iac-modules", "contoso/iac-modules"), SHARED_CALL],
        "edits": {
            ".github/workflows/terragrunt.yml": [
                ("\n  plan:\n    needs: changes\n", "\n" + TERRAGRUNT_GUARD + "\n    needs: changes\n"),
                ("    if: ${{ !cancelled() && needs.changes.outputs.relevant != 'false' }}",
                 "    if: ${{ !cancelled() && vars.AZURE_CLIENT_ID_PLATFORM != '' && needs.changes.outputs.relevant != 'false' }}"),
                ("""  # In Azure DevOps a skipped apply stage left the run green, so eight applies
  # in a row never happened and nothing said so. An apply that was due and did
  # not succeed has to be red.
  gate:
    needs: [plan, apply]
    if: always() && github.event_name != 'pull_request'""",
                 """  # A skipped apply must not leave the run green - otherwise applies can fail
  # to happen for weeks and nothing says so. An apply that was due and did not
  # succeed has to be red.
  gate:
    needs: [plan, apply]
    if: always() && github.event_name != 'pull_request' && vars.AZURE_CLIENT_ID_PLATFORM != ''"""),
            ],
            ".github/workflows/drift.yml": [
                ("jobs:\n  drift:\n    runs-on: ubuntu-latest\n",
                 "jobs:\n  # Skipped until AZURE_CLIENT_ID_PLATFORM is filled in - see terragrunt.yml.\n"
                 "  drift:\n    if: vars.AZURE_CLIENT_ID_PLATFORM != ''\n    runs-on: ubuntu-latest\n"),
            ],
        },
    },
    "template-iac-m365": {
        "live": "iac-m365",
        "copy": [".github/scripts", ".github/workflows/m365.yml", ".github/workflows/drift.yml",
                 "src/Runner", "src/Gaps", "src/Resolve.psm1", "src/Schema.psm1", "src/Order.psd1", "src/Routing.psd1",
                 "tests", "tools/Test-Configuration.ps1", "tools/Update-SelfServicePurchaseProducts.ps1"],
        "keep": [],
        "replace": [SHARED_CALL, ("noreply@bearclave.cz", "noreply@contoso.com")],
        "edits": {
            ".github/workflows/m365.yml": [
                ("""# Everything runs on ubuntu-latest. Azure DevOps used a Windows agent because
# nobody had checked PnP.PowerShell and the SharePoint resources anywhere else;
# after the scope was narrowed to the tenant settings, the whole plan was
# verified on Unix and Windows would cost twice the quota for nothing.""",
                 """# Everything runs on ubuntu-latest. The whole plan was verified on Unix and a
# Windows runner would cost twice the quota for nothing."""),
                ("""      - name: Configuration against the schemas
        shell: pwsh
        run: |
          ./tools/Test-Configuration.ps1""",
                 """      # In the template there is only the example file; a customer repository
      # has the real tenant.psd1 next to it and this does nothing.
      - name: Configuration against the schemas
        shell: pwsh
        run: |
          if (-not (Test-Path ./tenant.psd1)) { Copy-Item ./tenant.example.psd1 ./tenant.psd1 }
          ./tools/Test-Configuration.ps1"""),
                ("\n  plan:\n    needs: [changes, validate]\n",
                 """
  # Everything from here on signs in to the tenant. Until AZURE_CLIENT_ID is
  # filled in the jobs are skipped, so a freshly created repository is green
  # and only the checks that need no tenant run.
  plan:
    needs: [changes, validate]
"""),
                ("""      ${{ !cancelled()
          && needs.changes.outputs.relevant != 'false'""",
                 """      ${{ vars.AZURE_CLIENT_ID != ''
          && !cancelled()
          && needs.changes.outputs.relevant != 'false'"""),
                ("""  # In Azure DevOps a skipped apply stage left the run green. An apply that was
  # due and did not succeed has to be red.
  gate:
    needs: [plan, apply]
    if: always() && github.event_name != 'pull_request'""",
                 """  # A skipped apply must not leave the run green. An apply that was due and did
  # not succeed has to be red.
  gate:
    needs: [plan, apply]
    if: always() && github.event_name != 'pull_request' && vars.AZURE_CLIENT_ID != ''"""),
            ],
            ".github/workflows/drift.yml": [
                ("jobs:\n  drift:\n    runs-on: ubuntu-latest\n",
                 "jobs:\n  # Skipped until AZURE_CLIENT_ID is filled in - see m365.yml.\n"
                 "  drift:\n    if: vars.AZURE_CLIENT_ID != ''\n    runs-on: ubuntu-latest\n"),
            ],
        },
    },
    "template-iac-modules": {
        "live": "iac-modules",
        "copy": [".github/workflows/validate.yml", ".github/scripts", ".github/tool-versions.env",
                 ".tflint.hcl", ".trivyignore.yaml", "conventions.yaml",
                 "_shared", "data", "identity", "monitoring", "platform", "security", "sentinel", "web"],
        # The live description names the tenant's own domains.
        "keep": ["identity/entra-users/variables.tf"],
        "replace": [SHARED_CALL,
                    ("(job conventions in validate.yml, rules in bearclave/ci). level: error\n"
                     "# fails the check, warn only reports, off skips the rule. An exception needs\n"
                     "# its reason; what the rules mean is in the README of bearclave/ci.",
                     "(job conventions in validate.yml, the rules in .github/workflows/\n"
                     "# conventions.yml). level: error fails the check, warn only reports, off skips\n"
                     "# the rule. An exception needs its reason.")],
        "edits": {},
    },
}


def tracked(repo: pathlib.Path) -> list:
    out = subprocess.run(["git", "ls-files"], cwd=repo, capture_output=True, text=True, check=True).stdout
    return out.splitlines()


def apply_rules(text: str, rules: dict, path: str) -> str:
    for old, new in rules["replace"]:
        text = re.sub(old, new, text) if old.startswith("uses: ") else text.replace(old, new)
    for old, new in rules["edits"].get(path, []):
        if text.count(old) != 1:
            sys.exit(f"{path}: the text an edit expects is not there once - the live file changed shape:\n{old[:120]}")
        text = text.replace(old, new)
    return text


def plan(base: pathlib.Path, name: str, rules: dict, template: pathlib.Path):
    """(path, new content or None for removal) for every file out of date."""
    live = base / rules["live"]
    live_files, template_files = set(tracked(live)), set(tracked(template))
    selected = lambda p: any(p == c or p.startswith(c.rstrip("/") + "/") for c in rules["copy"])
    out = []
    for path in sorted(f for f in live_files if selected(f) and f not in rules["keep"]):
        source = live / path
        try:
            text = apply_rules(source.read_text(encoding="utf-8"), rules, path)
        except UnicodeDecodeError:
            if not (template / path).exists() or not filecmp.cmp(source, template / path, shallow=False):
                out.append((path, source.read_bytes()))
            continue
        if FORBIDDEN.search(text):
            line = next(line for line in text.splitlines() if FORBIDDEN.search(line))
            sys.exit(f"{rules['live']}/{path} would bring the organisation into {name}: {line.strip()[:120]}")
        current = (template / path).read_text(encoding="utf-8") if (template / path).exists() else None
        if current != text:
            out.append((path, text))
    for path in sorted(f for f in template_files if selected(f) and f not in live_files and f not in rules["keep"]):
        out.append((path, None))
    return out


def main(args) -> int:
    check = "--check" in args
    rest = [a for a in args if not a.startswith("--")]
    base = pathlib.Path(rest[0]) if rest and pathlib.Path(rest[0]).is_dir() and not rest[0].startswith("template-") \
        else ROOT.parent
    targets = {}
    for a in rest:
        if a.startswith("template-"):
            name, _, where = a.partition("=")
            targets[name] = pathlib.Path(where) if where else base / name
    targets = targets or {n: base / n for n in TEMPLATES}
    names = list(targets)
    stale = 0
    for name in names:
        changes = plan(base, name, TEMPLATES[name], targets[name])
        for path, content in changes:
            stale += 1
            target = targets[name] / path
            if check:
                print(f"{name}: {'remove' if content is None else 'update'} {path}")
            elif content is None:
                target.unlink()
                print(f"{name}: removed {path}")
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                (target.write_bytes if isinstance(content, bytes) else
                 lambda t: target.write_text(t, encoding="utf-8"))(content)
                print(f"{name}: wrote {path}")
    export = [sys.executable, str(ROOT / "export-for-templates.py")] + (["--check"] if check else []) + \
        [str(targets[n]) for n in names]
    rc = subprocess.run(export).returncode
    return 1 if (check and stale) or rc else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
