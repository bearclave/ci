# Repository types

<!-- Standard of bearclave/governance (standards/repo-types.md). In other repositories this is a managed copy - change it in governance. -->

What a repository must have, by the custom property `pipeline` it calls.
On top of what every repository has ([repositories.md](https://github.com/bearclave/governance/blob/main/standards/repositories.md#what-every-repository-contains)).
A missing item is a `Debt` issue in that repository.

## IaC: `terragrunt`, `terragrunt-home`, `m365-dsc`

| Item | Rule |
|---|---|
| Pipeline | `ci.yml` from [templates/workflows/](https://github.com/bearclave/governance/tree/main/standards/templates/workflows/); plan on the pull request, apply from `main` ([pipelines.md](https://github.com/bearclave/governance/blob/main/standards/pipelines.md)) |
| Drift | weekly, read-only identity, red on any difference (`drift.yml`, or the schedule in `ci.yml` for `terragrunt-home`) |
| Environment | `<subject>-prod` (`azure-prod`, `github-prod`, `home-prod`, `m365-prod`), deployment branches `main` only, in `iac-github` |
| Identities | a plan and an apply identity ([identities.md](https://github.com/bearclave/governance/blob/main/standards/identities.md)), variables `AZURE_CLIENT_ID_PLAN` and `AZURE_CLIENT_ID_APPLY` |
| Versions | `.github/tool-versions.env` (Terraform, Terragrunt, trivy with sha256); providers exact in `account.hcl` ([toolchain.md](https://github.com/bearclave/governance/blob/main/standards/toolchain.md)) |
| Lock files | `.terraform.lock.hcl` committed in every unit, hashes `linux_amd64` and `darwin_arm64` |
| Layout | `root.hcl`, `account.hcl`, `_common/` (`units/`, `deps/`), `infrastructure/`; no `tools/`, no `deploy/` ([code/terragrunt.md](https://github.com/bearclave/governance/blob/main/standards/code/terragrunt.md)) |
| Modules | from `iac-modules` at a tag (`modules_ref` in `account.hcl`), in `_common/units/<module>.hcl`, never a local path |
| Properties | `touches-production=true`; exact pin of `bearclave/ci` (`@vX.Y.Z`) |
| Docs | README Pipeline table with every trigger and identity; `docs/setup.md` (bootstrap as a procedure with its commands: state, identities, environment), `docs/manual-steps.md`, `docs/out-of-scope.md` where something is not in code |
| State moves | `.github/state-moves.yaml` while a moved unit waits for its apply |

## Modules: `terraform-modules`

| Item | Rule |
|---|---|
| Pipeline | fmt, validate, tflint, trivy config, `terraform test`, terraform-docs up to date, conventions |
| Layout | `<platform>/<module>/` (`azure/key-vault/`), `<platform>/_common/`; `docs/catalog.md` generated; skeleton [templates/terraform-module/](https://github.com/bearclave/governance/tree/main/standards/templates/terraform-module/) |
| Rules | [code/terraform.md](https://github.com/bearclave/governance/blob/main/standards/code/terraform.md): no defaults, no `any`, provider lower bounds, a README with the terraform-docs block per public module, tests in `_common/` |
| Releases | one tag for the repository, `vX.Y.Z` ([versioning.md](https://github.com/bearclave/governance/blob/main/standards/versioning.md)); consumers pin it in `account.hcl` |
| Properties | `touches-production=true` while a consumer reads `main`; `false` once every consumer pins a tag |

## Application: `static-web-app`, `container`

| Item | Rule |
|---|---|
| Tests | in `tests/` (or the site's `tools/` checks for a static site), run in the pipeline on every pull request |
| Deploy | from `main` only, in the environment `prod` of the repository (deployment branches `main` only) |
| Identity | a deploy identity federated on `environment:prod` ([identities.md](https://github.com/bearclave/governance/blob/main/standards/identities.md)) |
| Smoke test | after every deployment: the site or the service answers and serves the new version; a failure makes the run red. Where GitHub cannot reach the host (`app-home` on the NAS), the image's `HEALTHCHECK` on the host stands in and the README says how to read it |
| Artefact | a container image tagged with the version (`vX.Y.Z`) and the commit, never only `:main`; a site is its commit |
| Rollback | redeploy the previous tag or revert the commit; written in the README |
| Dependencies | `.github/dependabot.yml` for its ecosystems ([templates/dependabot.yml](https://github.com/bearclave/governance/blob/main/standards/templates/dependabot.yml)) |
| Properties | `touches-production=true`; exact pin of `bearclave/ci` |
| Docs | README: what runs where, how to run it locally, the smoke test, rollback |

A new kind of application (Azure Functions, a container on another host)
gets its shared workflow in `ci` and its row here before the first
deployment.

## Know-how, tools and content: `checks`, `content-sentinel`

`governance`, `handbook`, `ops-tools`, `.github`, `vs-code-extensions`,
`app-office-templates` (`checks`); `content-sentinel` (its own pipeline:
Sentinel lint, generated views up to date).

| Item | Rule |
|---|---|
| Pipeline | `checks.yml`: security scan, ESLint where there is JavaScript, the repository's own command (tests, generators up to date); `content-sentinel.yml` |
| Docs | Markdown rules of [documentation.md](https://github.com/bearclave/governance/blob/main/standards/documentation.md#markdown); links work |
| Releases | only where something is consumed by reference ([versioning.md](https://github.com/bearclave/governance/blob/main/standards/versioning.md)) |
| Properties | `touches-production=false`; `direct-commit=true` only where the owner writes straight to `main` |

## Shared workflows: `ci`

| Item | Rule |
|---|---|
| Pipeline | its own `ci.yml`: unit tests, self-tests of the checks, generated workflows up to date (`tools/build.py --check`) |
| Releases | `vX.Y.Z` and the major tag `vX` ([versioning.md](https://github.com/bearclave/governance/blob/main/standards/versioning.md)) |
| Identities | one bot identity per workflow purpose, each in its own environment |
| Docs | README per workflow; `docs/identities.md`, the inventory of every identity a workflow uses |
| Properties | `touches-production=true`: an apply pipeline runs its code |

## Templates: `template`

Copied to customers: nothing names the organisation or a tenant, local
copies of the shared workflows, a
version per handover ([versioning.md](https://github.com/bearclave/governance/blob/main/standards/versioning.md)).
