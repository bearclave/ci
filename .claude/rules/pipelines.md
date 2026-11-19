---
paths:
  - ".github/**"
---

# Pipelines

<!-- Standard of bearclave/governance (standards/pipelines.md). In other repositories this is a managed copy - change it in governance. -->

All pipeline logic lives once, in [`bearclave/ci`](https://github.com/bearclave/ci),
as reusable workflows. A repository holds only short callers.

## The callers in a repository

| File | Trigger | Jobs and check names |
|---|---|---|
| `.github/workflows/ci.yml` | pull request, push to `main`, manual | `security` → **`security / scan`**; `pipeline` → its jobs and **`pipeline / gate`** |
| `.github/workflows/pr.yml` | pull request opened, edited, synchronised, reopened, ready | `pr` → **`pr / check`** (managed file) |
| `.github/workflows/drift.yml` | weekly schedule, manual | `drift` → the drift run of the pipeline (only where something is applied) |

The checks in bold are required on `main` by the organisation rulesets in
`bearclave/iac-github`. Every repository has all three; a repository with
nothing to build calls the `checks` pipeline, whose `gate` passes when its
checks do.

`ci.yml` passes the repository's variables and secrets explicitly
(`with:` / `secrets:`), never `secrets: inherit`. The caller owns the
triggers and the `concurrency` group; the shared workflow owns everything
else. Templates for each pipeline: [templates/workflows/](https://github.com/bearclave/governance/tree/main/standards/templates/workflows/).

## The shared workflows

| Workflow | Used by | Does |
|---|---|---|
| `security.yml` | every repository | gitleaks over the history, actionlint, zizmor, shellcheck |
| `pr.yml` | every repository | title and branch format, labels, assignee, description sections |
| `checks.yml` | `checks` | optional extra command, then `gate` |
| `terragrunt.yml` / `terragrunt-drift.yml` | `iac-azure`, `iac-github` | fmt, validate, conventions, Sentinel lint, plan of affected units with trivy, PR comment, apply from `main`, destroy held back, gate |
| `terragrunt-home.yml` | `iac-home` | plan and apply of the router through WireGuard, certificate renewal, drift |
| `terraform-modules.yml` | `iac-modules` | fmt, validate, tflint, trivy, conventions |
| `m365-dsc.yml` / `m365-drift.yml` | `iac-m365` | schema check, Pester, PSScriptAnalyzer, plan of affected types, apply, tombstones |
| `static-web-app.yml` | `app-web-*` | the site's checks, deploy to the Static Web App from `main` |
| `container.yml` | `app-home` | lint and tests in the image, push to GHCR from `main` |
| `content-sentinel.yml` | `content-sentinel` | Sentinel lint, generated views up to date |
| `conventions.yml`, `sentinel-lint.yml` | inside the pipelines above | rule checks driven by the repository's YAML |

Versions: callers use `@v2`; repositories with `touches-production=true`
pin the exact release (`@v2.3.1`) and Dependabot proposes the bump, so a
change to an apply pipeline reaches production only through a pull request
with a plan. See [versioning.md](https://github.com/bearclave/governance/blob/main/standards/versioning.md).

Templates of the organisation (`template-iac-*`) cannot reach the private
`bearclave/ci`: they carry local copies of the shared workflows, written by
`ci/tools/export-for-templates.py`, and call them with `uses: ./.github/workflows/...`.
Their check names are the same.

## Rules for every workflow

- **Plan on the pull request, apply from `main`.** Apply runs in a protected
  environment (`azure-prod`, `m365-prod`, `home-prod`, `github-prod`) whose
  deployment branch policy allows `main` only.
- **Apply only what the plan proposed**, and only when it proposed
  something; a plan that destroys or replaces waits for a manual run with
  `allow_destroy` where the pipeline supports it.
- **A due apply that did not happen is red.** Every pipeline ends with a
  `gate` job (`if: always()`) that checks the results of what it needs.
  A skipped job passes a required check, so the gate - never a skippable
  job - is the required one.
- **No `paths` filter on `pull_request`** of a workflow with a required
  check: a pull request it never runs for waits forever. Skip inside
  instead (a `changes` job).
- `concurrency`: a pull request cancels its previous run; runs on `main`,
  manual runs and drift share one group per state and never overlap.
- `timeout-minutes` on every job; `permissions` minimal, at job level.
- Tools as pinned release binaries checked against sha256; actions pinned
  to a commit SHA ([security.md](https://github.com/bearclave/governance/blob/main/standards/security.md)).
- The plan goes into one pull request comment that is rewritten on every
  push, and into the job summary; raw plan files stay on the runner.
- Drift runs weekly, plans the whole scope and fails on any difference.

## Changing a shared workflow

1. Change it in `bearclave/ci` (template or script, then `tools/build.py`),
   test it from the pull request's own run.
2. Merge, release (`v2.x.y`, move `v2`).
3. Repositories on `@v2` get it at once; pinned repositories get a
   Dependabot pull request with a plan.
4. `tools/export-for-templates.py` for the templates, a pull request each.
