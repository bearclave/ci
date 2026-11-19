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
| `checks.yml` | `checks` | ESLint where there is JavaScript, optional extra command, then `gate` |
| `terragrunt.yml` / `terragrunt-drift.yml` | `iac-azure`, `iac-github` | fmt, validate, conventions, Sentinel lint, plan of affected units with trivy, PR comment, apply from `main`, destroy held back, gate |
| `terragrunt-home.yml` | `iac-home` | plan and apply of the router through WireGuard, certificate renewal, drift |
| `terraform-modules.yml` | `iac-modules` | fmt, validate, tflint, trivy config, conventions |
| `m365-dsc.yml` / `m365-drift.yml` | `iac-m365` | schema check, Pester, PSScriptAnalyzer, plan of affected types, apply, tombstones |
| `static-web-app.yml` | `app-web-*` | common site checks, ESLint, the site's own checks, deploy to the Static Web App from `main` |
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

## Checks and their exceptions

Every check of the organisation, the pipeline that runs it, where it is
configured and where an exception goes. Nothing is checked outside this
table; a new check comes with its row.

| Check | Pipeline (job) | Config | Exceptions |
|---|---|---|---|
| gitleaks | `security` (`scan`) | `.gitleaks.toml` (default rules + allowlists) | `[[allowlists]]` in `.gitleaks.toml`, `description` = reason + `review:` line |
| actionlint | `security` (`scan`) | - | none: fix the workflow |
| zizmor | `security` (`scan`) | in `security.yml` | none in a repository |
| shellcheck | `security` (`scan`), and inside `run:` through actionlint | `--severity=warning` | `# shellcheck disable=SCxxxx # reason` on the line |
| title, branch, description, labels | `pr` (`check`) | `src/pr_check.py` of ci | none |
| root layout | `pr` (`check`) | `standards/layout.yaml` | `layout.exceptions` in `.github/conventions.yaml` (`review` optional) |
| conventions | `terragrunt`, `terragrunt-home`, `terraform-modules` (`conventions`) | `.github/conventions.yaml` `rules` | `exceptions` in `.github/conventions.yaml` |
| Sentinel lint | `terragrunt`, `content-sentinel` (`sentinel`) | `.github/sentinel-lint.yaml` | `exceptions` in `.github/sentinel-lint.yaml` |
| exception reviews | the conventions and Sentinel jobs, `security` for gitleaks | the files of this column | - |
| `terragrunt hcl fmt` / `hcl validate` | `terragrunt`, `terragrunt-home` | - | none |
| trivy over the plans | `terragrunt`, `terragrunt-drift` (`plan`) | HIGH, CRITICAL | `.github/trivyignore.yaml`, `paths` = `<unit>/tfplan.json` |
| `terraform fmt` / `validate` | `terraform-modules` (`terraform`) | - | none |
| tflint | `terraform-modules` (`terraform`) | `.tflint.hcl` (managed, below) | a rule switched off in the repository section of `.tflint.hcl`, or `# tflint-ignore: <rule> # reason, review YYYY-MM-DD` above the block |
| trivy config | `terraform-modules` (`terraform`) | HIGH, CRITICAL | `.github/trivyignore.yaml` |
| ESLint | `static-web-app`, `checks` (`eslint`) | `eslint.config.mjs` (managed, below) | the repository section of `eslint.config.mjs`, each entry with a comment why |
| common site checks | `static-web-app` (`site`) | `src/staticwebapp.config.json` | none: fix the site |
| site checks of the repository | `static-web-app` (`site`) | `site-checks` input | in the repository's script |
| PSScriptAnalyzer, Pester | `m365-dsc` | `.github/pssa.psd1` | `.github/pssa.psd1` (`iac-m365` is being retired) |
| ruff, tests | `container` (in the `Dockerfile`) | `[tool.ruff]` in `pyproject.toml` | `[tool.ruff.lint]` of the repository |

**One exception format.** Wherever the format is ours, an exception names
what (`rule`; `id` in trivy), where (`path`; `paths` in trivy), why
(`reason`; `statement` in trivy) and when it is looked at again: `review`,
a date (YYYY-MM-DD), six months ahead when it is written. A missing reason
or a date that is not a date fails the check; a review date that has
passed, or a missing one, is a warning, and the exception stays in force until
someone decides: renew the date with a sentence on why it still holds, or
fix the finding and remove the exception.

```yaml
# .github/conventions.yaml (sentinel-lint.yaml: rule = the check, path = the rule file)
exceptions:
  - rule: no-hardcoded-location
    path: mg-corp/policy-assignments/terragrunt.hcl
    reason: The allowed-locations policy lists the regions it allows.
    review: 2027-05-19

# .github/trivyignore.yaml
misconfigurations:
  - id: AVD-AZU-0013
    paths: [mg-corp/.../key-vault/tfplan.json]
    statement: Why the finding is accepted.
    review: 2027-05-19
```

```toml
# .gitleaks.toml
[[allowlists]]
description = """
Why this is not a secret.
review: 2027-05-19
"""
regexes = ['''...''']
```

**tflint** runs where the Terraform is: in `terraform-modules`. A
Terragrunt repository holds units (`terragrunt.hcl`) and no `.tf` of its
own - its units take every module from `iac-modules` at `modules_ref`, and
that module was linted there, with the same base config, before the ref
existed. So tflint does not run in `terragrunt*`; what reaches the units is
covered by `hcl validate`, the plan and trivy over it.

**Managed lint configs**, written by `tools/sync.py <repository>` like
`.gitattributes`: the base above the marker is the same everywhere, the
section below it belongs to the repository and is kept.

| File | Written for | Base |
|---|---|---|
| `.tflint.hcl` | repositories with `*.tf` (not templates) | the `terraform` preset `recommended`, the `azurerm` ruleset at a pinned version, `azurerm_resources_missing_prevent_destroy` on |
| `eslint.config.mjs` | repositories with `*.js`/`*.mjs`/`*.cjs` (not templates) | `@eslint/js` recommended, browser and Node globals |

ESLint runs the version pinned in the repository's `package.json`
`devDependencies` where there is one, otherwise the version pinned in
`bearclave/ci` through `npx`. Lint errors that are real bugs are fixed;
everything else is configured in the repository section, not rewritten.

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
