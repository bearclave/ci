# ci

Documentation is in `README.md`. The organisation standards are in
`.claude/rules/` (managed copies from `bearclave/governance`): branches,
commits, pull requests, issues, versioning, security and the language rules.
This file holds only what is specific to this repository.

## This repository

- **What a merge does:** nothing is deployed. Callers on `@v2` get a
  change when the owner releases and `v2` moves; pinned callers through
  the `ci-pins` pull request ([docs/releasing.md](../docs/releasing.md)).
  An agent never merges, tags, moves `v2` or runs `tools/release.sh` (but
  for `--dry-run`) or `tools/move-major-tag.sh`.
- **Local checks before a pull request:**
  `python3 -m unittest discover -s tests`,
  `python3 src/pr_check.py --self-test`,
  `python3 src/layout_check.py --self-test`, `tools/build.py --check`;
  actionlint and zizmor over `.github/workflows`.
- **Do not touch:** the generated workflows (edit `templates/` and `src/`,
  then `tools/build.py`); `src/layout.yaml` is a managed copy from
  governance (`tools/sync.py ci`).
- **Review:** `/code-review` and `/security-review` (the repository touches
  production: a release reaches every caller).
- **Releases:** `v2.Y.Z` and the moving `v2`, made by the owner
  ([docs/releasing.md](../docs/releasing.md)).
