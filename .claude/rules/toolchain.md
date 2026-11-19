---
paths:
  - ".github/**"
  - "**/*.hcl"
  - "**/*.tf"
---

# Toolchain

<!-- Standard of bearclave/governance (standards/toolchain.md). In other repositories this is a managed copy - change it in governance. -->

What the pipelines run on, which tools and providers they use, where each
version is pinned and how it is bumped. The goal is a small, boring
toolchain: one runner image, as few tools as possible, every version in one
place.

## Runners

| Rule | Why |
|---|---|
| GitHub-hosted runners only, `runs-on: ubuntu-24.04` | a fixed image; `ubuntu-latest` moves to a new release on its own |
| No self-hosted runners | nothing to patch, nothing that keeps state between runs ([security.md](https://github.com/bearclave/governance/blob/main/standards/security.md)) |
| The home network is reached from the hosted runner through WireGuard | `terragrunt-home.yml`; no runner at home |
| A bump of the image is a `deps` pull request in `bearclave/ci` | all workflows move together and the plan shows the effect |

**What a job may take from the image** - nothing else is assumed to be there:

| Tool | Used for |
|---|---|
| `bash`, coreutils, `curl`, `tar`, `unzip`, `jq`, `git` | scripts and downloads |
| `python3` (standard library only) | the conventions and Sentinel lint scripts |
| `pwsh` | the Microsoft365DSC pipelines |
| `az` | sign-in by OIDC and Azure calls (through `azure/login`) |
| `gh` | pull request comments and labels |
| `node` / `npx` | the Static Web Apps CLI and site builds, at a pinned package version |

Anything else is downloaded at a pinned version and checked against its
sha256 before it runs.

## Tools and where their version lives

One file, **`tool-versions.env` in `bearclave/ci`**, holds the version and
sha256 of every downloaded tool. It is released with `ci`, so a repository
on `@v2.3.1` gets exactly the tools of that release. Repositories keep no
copy of their own.

| Tool | Installed by | Used in |
|---|---|---|
| Terraform | `hashicorp/setup-terraform` with the pinned version | `terragrunt*`, `terraform-modules` |
| Terragrunt | release binary + sha256 | `terragrunt*` |
| TFLint | release binary + sha256 | `terraform-modules` |
| Trivy | release binary + sha256 | `terragrunt*`, `terraform-modules` |
| gitleaks, actionlint, zizmor, shellcheck | release binary + sha256 | `security` |
| Microsoft365DSC | `Install-Module -RequiredVersion` | `m365-*` |
| Pester, PSScriptAnalyzer | `Install-Module -RequiredVersion` | `m365-*` |
| Static Web Apps CLI | `npx` at a pinned version | `static-web-app` |

- **Exception:** `template-iac-*` cannot read the private `bearclave/ci` and
  carry their own `tool-versions.env`, written by
  `ci/tools/export-for-templates.py` from the same file.
- **Bumps:** Dependabot does not read `tool-versions.env`. A weekly workflow
  in `bearclave/ci` compares it with the upstream releases and opens one
  `deps` pull request with the new versions and sha256, only for releases at
  least 7 days old (the same rule as Dependabot).
- **New tool:** only when no existing tool or the image can do the job; it
  goes into `tool-versions.env` and the table above in the same pull request.

## Terraform providers

| Where | What is pinned | Why |
|---|---|---|
| Modules (`iac-modules`, `template-iac-modules`) | a lower bound only: `>= 5.0` | a module works with any newer provider the configuration chooses |
| Configuration (`iac-azure`, `iac-home`, `iac-github`, `template-iac-terragrunt`) | the exact version, once, in `root.hcl` (`locals.versions`), generated into every unit | one place per repository; every unit runs the same provider |
| `.terraform.lock.hcl` | committed in every unit, with hashes for `linux_amd64` and `darwin_arm64` | the provider binary is verified, not only its version |

- Lock files are refreshed with
  `terragrunt run --all providers lock -platform=linux_amd64 -platform=darwin_arm64`
  in the same pull request that bumps a version.
- A provider bump is a `deps` pull request with its plan; a major version is
  its own pull request with the upgrade guide linked in Why.
- `required_version` of Terraform is a lower bound in modules and follows
  `tool-versions.env` in configuration.

## Locally

The Mac runs the same versions as the pipeline: `mise` reads the versions
from `tool-versions.env` of `bearclave/ci`, so a local plan does not run on a
different Terraform. Homebrew installs only what the pipeline does not use.

## Where it is documented

| What | Where |
|---|---|
| These rules | this file |
| Versions and sha256 | `bearclave/ci` `tool-versions.env` |
| Exact provider versions | `root.hcl` of each configuration repository |
| What a pipeline runs | its row in [pipelines.md](https://github.com/bearclave/governance/blob/main/standards/pipelines.md) and the README of `bearclave/ci` |

A README of a repository does not list tool versions; its Pipeline section
names the shared workflow and links here.
