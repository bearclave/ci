---
paths:
  - ".github/**"
  - "**/*.hcl"
  - "**/*.tf"
---

# Identities

<!-- Standard of bearclave/governance (standards/identities.md). In other repositories this is a managed copy - change it in governance. -->

Which identity a workflow signs in with, what it may do and how it is
federated. Every identity a GitHub workflow uses is listed, with its
credentials and permissions, in the inventory
[`bearclave/ci` `docs/identities.md`](https://github.com/bearclave/ci/blob/main/docs/identities.md);
this page holds the rules the inventory follows.

## When a new identity is created

**One identity per pipeline and purpose.** A new identity is created when:

- a repository gets a pipeline that signs in to Azure, Entra, Microsoft 365
  or a GitHub App key (a new `iac-*` repository gets two: plan and apply);
- an existing workflow gains a purpose its identity does not have (`deps`
  and `ci-pins` got `sp-ci-deps` instead of reusing `sp-ci-major-tag`);
- one identity would otherwise serve a read-only and a writing job.

Never reused across repositories, never shared between a pull request and an
apply, never a person's account in a pipeline.

## Types

| Type | Used by | Runs on | May |
|---|---|---|---|
| **plan** | the plan of a pull request and the weekly drift | `pull_request`, schedule on `main` | read everything it plans; write nothing it manages |
| **apply** | the apply after a merge | the production environment only | write what the repository manages, nothing else |
| **bot** | workflows that act on GitHub as `bearclave-bot` (`major-tag`, `deps`, `ci-pins`, `project-sync`) | its own environment in `ci` | read one secret: the bot's key in `kv-githubbot` |
| **deploy** | an application's deployment | the environment `prod` of the application | deploy its one resource |

A bot identity is one per workflow purpose, even when they read the same
key: the credential decides which workflow can sign in, and a narrower
federation is a smaller blast radius.

## Permissions per type

| Permission | plan | apply | bot | deploy |
|---|---|---|---|---|
| Terraform state | Storage Blob Data Reader on its own container; Contributor on its own container only where the first plan of a new unit must create the blob - never on the whole account | Storage Blob Data Contributor on its own container | - | - |
| Key Vault | Key Vault Secrets User on the read-only vault of its pipeline (`kv-githubro`, `kv-home`) | Key Vault Secrets User on the vault of its pipeline (`kv-github`, `kv-home`) | Key Vault Secrets User on `kv-githubbot` | - |
| Azure RBAC | Reader on the scope it plans | the narrowest role that applies (Contributor, Owner only where it assigns roles) on the scope it manages | - | the narrowest role that deploys the resource (e.g. `Website Contributor` / Static Web App deployment, not Contributor on the resource group) |
| Entra roles, Graph | Global Reader, `*.Read.All` application permissions | the admin roles and `*.ReadWrite.*` permissions the code manages, each named in the identity's description | - | - |
| GitHub App | the read-only App (`bearclave-iac-github-read`): all permissions `read` | the App that writes (`bearclave-iac-github`) | `bearclave-bot`: contents, pull requests, issues, workflows `write`, projects `write`, major-tag bypass | - |

- No deploy token, client secret or certificate read by a plan identity.
- A Tier-0 permission (Owner on a management group, Privileged Role
  Administrator, `RoleManagement.ReadWrite.Directory`, `Directory.ReadWrite.All`)
  only on an **apply** identity, federated only on its environment.
- The description of the application in `iac-azure` says what it may do;
  the inventory repeats it.

## Federated credentials

| Type | Subject(s) | Never |
|---|---|---|
| plan | `<prefix>:pull_request` and `<prefix>:ref:refs/heads/main` (the drift runs on a schedule from `main`) | an environment |
| apply | `<prefix>:environment:<prod environment>` only (`azure-prod`, `github-prod`, `home-prod`, `m365-prod`) | `ref:refs/heads/main`, `pull_request` |
| bot | `<prefix>:environment:<workflow environment>` of `ci` (`major-tag`, `deps`, `project-sync`) | a branch or `pull_request` |
| deploy | `<prefix>:environment:prod` | a branch or `pull_request` |

- `<prefix>` is the immutable subject with the organisation and repository
  IDs: `repo:bearclave@328058774/<repository>@<repository id>`. A credential
  cannot pass to a re-created repository; a renamed repository needs a new
  credential under its new name (the old one is removed after the rename).
- An environment named in a subject has a deployment branch policy that
  allows `main` only ([`bearclave/iac-github`](https://github.com/bearclave/iac-github)),
  so only a run of `main` reaches the identity.
- The job that signs in names the environment (`environment:` in the shared
  workflow); a job without it gets a `ref` subject and is refused.

## Names

| What | Pattern | Example |
|---|---|---|
| plan | `sp-tf-<pipeline>-plan` (`sp-tf-plan` for `iac-azure`) | `sp-tf-home-plan` |
| apply | `sp-tf-<pipeline>` (`sp-tf-platform` for `iac-azure`) | `sp-tf-github` |
| bot | `sp-ci-<purpose>`, `sp-<purpose>` | `sp-ci-deps`, `sp-project-sync` |
| deploy | `sp-<kind>-<subject>-prod` | `sp-swa-sro-prod` |
| federated credential | `gh-<application without sp->-<purpose>` | `gh-tf-home-plan` |
| GitHub variable | `AZURE_CLIENT_ID_PLAN`, `AZURE_CLIENT_ID_APPLY`, `AZURE_CLIENT_ID_<PURPOSE>` | `AZURE_CLIENT_ID_DEPS` |

Descriptions are English and say what the identity may do and what it may
not.

## Where they are declared

| What | Where |
|---|---|
| The application and its service principal | `iac-azure` `tenant/applications` |
| Federated credentials | `iac-azure` `tenant/federated-credentials` |
| Azure RBAC, Key Vault and state access | the unit of the resource, `role_assignments` |
| Entra roles, Graph permissions | `iac-azure` `tenant/directory-roles`, `tenant/graph-permissions` |
| Client ID in GitHub | repository variable in `iac-github` (`repositories/<name>`) |
| GitHub Apps | `iac-github` `tools/github-apps.py`, `tools/bearclave-bot.py` (key in the Key Vault) |
| Inventory | `bearclave/ci` `docs/identities.md` |

A new identity is one pull request in `iac-azure` (application, credential,
role assignments) and then one in `iac-github` (the variable); the
inventory row comes with the workflow change in `ci` or with the first
pull request that uses it.

## Microsoft365DSC

`iac-m365` is being retired and keeps `sp-dsc-m365` as it is. A future
Microsoft365DSC pipeline follows the same split:

- **plan** (`Export`/`Test-DscConfiguration`): an application with the
  workloads' read permissions only (Global Reader, `*.Read.All`, Exchange
  `View-Only Organization Management`), federated on `pull_request` and
  `main`;
- **apply** (`Start-DscConfiguration`): the admin roles and write
  permissions, federated only on its production environment;
- certificate sign-in, where a workload refuses OIDC, only for the apply
  identity, the certificate in the Key Vault of the pipeline.
