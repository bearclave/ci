#!/usr/bin/env python3
"""Write docs/catalog.md of iac-modules from the module READMEs and variables.

Usage: catalog.py <iac-modules checkout> [--check]
--check exits 1 when docs/catalog.md differs from what would be written.
"""
import json
import pathlib
import re
import subprocess
import tempfile
import sys

# The platform folders, in the order of the catalog (governance
# code/terraform.md). A folder outside them - the root module _empty/, which
# a unit handed over to another repository runs - is no public module and
# stays out, as does a platform's _common/.
PLATFORMS = ["azure", "entra", "intune", "exchange", "purview", "github", "routeros"]


def what_it_manages(readme: pathlib.Path) -> str:
    text = readme.read_text()
    m = re.search(r"## What it manages\n\n(.+?)\n\n", text, re.S)
    return " ".join(m.group(1).split()) if m else ""


def interface(module: pathlib.Path) -> tuple[list[str], list[str]]:
    # Run outside the checkout: terraform-docs would otherwise pick up
    # .terraform-docs.yml there and inject the JSON into the README.
    out = subprocess.run(
        ["terraform-docs", "json", str(module)], capture_output=True, text=True, check=True, cwd=tempfile.gettempdir()
    )
    doc = json.loads(out.stdout)
    return [i["name"] for i in doc["inputs"]], [o["name"] for o in doc["outputs"]]


def render(root: pathlib.Path) -> str:
    # v7 keeps the modules under modules/<platform>/; older releases at the root.
    top = "modules/" if (root / "modules").is_dir() else ""
    lines = [
        "# Module catalog",
        "",
        "Generated from the module READMEs and variables; do not edit by hand.",
        "Source in a unit: `${modules}//" + top + "<platform>/<module>?ref=<modules_ref>`.",
        f"Shared building blocks: [azure/_common/](../{top}azure/_common/README.md).",
        "",
    ]
    for platform in PLATFORMS:
        mods = sorted(p.parent for p in (root / top / platform).glob("*/README.md") if not p.parent.name.startswith("_"))
        if not mods:
            continue
        lines += [f"## {platform}/", "", "| Module | What it manages | Inputs | Outputs |", "|---|---|---|---|"]
        for mod in mods:
            ins, outs = interface(mod)
            rel = mod.relative_to(root)
            lines.append(
                f"| [`{rel}`](../{rel}/README.md) | {what_it_manages(mod / 'README.md')} "
                f"| {', '.join(f'`{i}`' for i in ins)} | {', '.join(f'`{o}`' for o in outs)} |"
            )
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    root = pathlib.Path(sys.argv[1]).resolve()
    target = root / "docs" / "catalog.md"
    content = render(root)
    if "--check" in sys.argv[2:]:
        if not target.exists() or target.read_text() != content:
            print("docs/catalog.md is not up to date; run catalog.py", file=sys.stderr)
            return 1
        return 0
    target.parent.mkdir(exist_ok=True)
    target.write_text(content)
    return 0


if __name__ == "__main__":
    sys.exit(main())
