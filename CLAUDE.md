# ci

Documentation is in `README.md`. The organisation standards are in
`.claude/rules/` (managed copies from `bearclave/governance`): branches,
commits, pull requests, issues, versioning, security and the language rules.
This file holds only what is specific to this repository.

## This repository

- **What a merge does:** nothing is deployed. Every repository calls the workflows by `@v2`, so a merged change reaches them when the major tag moves (README, Releasing); move it only after the pull request's own run of the changed workflow is green. An agent may merge and tag `v2.Y.Z` when asked; moving `v2` is the owner's.
- **Local checks before a pull request:** `python3 -m unittest discover -s tests`, `python3 src/pr_check.py --self-test`, `./build.py --check`; actionlint and zizmor over `.github/workflows`. Edit `templates/` and `src/`, never the generated workflows.
- **Review:** `/code-review`.
- **Releases:** v2.Y.Z tags and the moving v2, see versioning.md.
