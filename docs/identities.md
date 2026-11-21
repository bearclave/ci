# Identities of the workflows

Every identity a GitHub workflow or GitHub App of `bearclave` uses: what it
is for, where it is federated, what it may do, where its key is and how it is
rotated. Identities of the tenant that no workflow uses (users, integrations, the
NAS, the Mac routines) are in `bearclave/iac-identity` and `iac-azure`.

The identities are code. In `iac-identity`: the Entra applications with
their federated credentials and Graph permissions
(`infrastructure/applications/app-registrations/pipelines/` and `sites/`),
their Entra roles (`infrastructure/roles/directory-roles/`). In `iac-azure`:
their Azure roles, in the units of the scopes they reach. In `iac-github`:
the variables and environments (`infrastructure/repositories/<repository>`).
This page is the map; the code is the truth.

## Rules

| Rule | Why |
|---|---|
| A workflow signs in to Azure by OIDC; no client secret anywhere | nothing to leak or rotate |
| A plan runs as a read-only identity, federated on `pull_request` and on `ref:refs/heads/main` (the plan of a push, the weekly drift) | a pull request may come from any branch and may change the caller's workflow |
| An identity that writes is federated **only** on `environment:<env>`; the environment's branch policy admits `main` (and release tags where a release runs) | no `pull_request`, no `ref:` subject reaches it |
| One identity per purpose | a leaked token reaches one thing |
| A GitHub App's key, App ID and installation ID live in a Key Vault that only its identities read | no App key in GitHub; the key of `bearclave-nas-runner` also on the NAS, which uses it (no workflow does) |
| The GitHub secrets are `MODULES_DEPLOY_KEY` and the environment secret `DEPLOY_KEY` of `app-home` | an SSH key cannot be federated |

The subject of a credential carries the repository's name and ID:
`repo:bearclave@328058774/<repository>@<repository id>:<context>`. A
renamed repository needs a credential under its new name before the rename.

## Entra applications

State of `main` of `iac-identity`, `iac-azure` and `iac-github` on
20. 11. 2026. The GitHub variable is a repository variable of the repository
in the third column. "Graph" lists application permissions of Microsoft
Graph.

| Identity | Purpose | Repository, workflow | Variable | Federated subject | Permissions |
|---|---|---|---|---|---|
| `sp-tf-plan` | plan of `iac-azure`: pull request, push, weekly drift | `iac-azure`: `ci.yml`, `drift.yml` | `AZURE_CLIENT_ID_PLAN` | `pull_request`, `ref:refs/heads/main` | Reader on MG `corp`, Global Reader, Storage Blob Data Contributor on the container `tfstate` (the state lock of a plan on `main`), Key Vault Reader on `kv-security` (public parts of certificates), Static Web App Secret Reader on the production and staging sites (the provider reads the deployment token) |
| `sp-tf-platform` | apply of `iac-azure` (Tier-0) | `iac-azure`: `ci.yml` job `apply` | `AZURE_CLIENT_ID_APPLY` | `environment:azure-prod` | Contributor, Resource Policy Contributor, Locks Contributor and Role Based Access Control Administrator with a condition (only the roles in `assignable_roles`, Owner only to named principals) on MG `corp`, never Owner; Storage Blob Data Contributor on the state account, Key Vault Certificates Officer on `kv-security`; no Entra role; Graph `Application.Read.All`, `Group.Read.All`, `User.Read.All` (lookups by name). Its own assignments on `corp` are changed by an Owner (`sg-azure-owners`), not by the pipeline |
| `sp-tf-github-plan` | plan of `iac-github`: pull request, push, weekly drift | `iac-github`: `ci.yml`, `drift.yml` | `AZURE_CLIENT_ID_PLAN` | `pull_request`, `ref:refs/heads/main` | Key Vault Secrets User on `kv-githubro`, Storage Blob Data Contributor on `tfstate-github` |
| `sp-tf-github` | apply of `iac-github` | `iac-github`: `ci.yml` job `apply` | `AZURE_CLIENT_ID_APPLY` | `environment:github-prod` | Key Vault Secrets User on `kv-github`, Storage Blob Data Contributor on `tfstate-github` |
| `sp-tf-home-plan` | plan of `iac-home`: pull request, push, schedule | `iac-home`: `ci.yml` (`terragrunt-home.yml`: validate, plan) | `AZURE_CLIENT_ID_PLAN` | `pull_request`, `ref:refs/heads/main` | Key Vault Secrets User on single `kv-home` secrets a plan needs (router account `tf-plan`, WireGuard peer `ci`, the Wi-Fi and WireGuard pre-shared keys, the Cloudflare DNS token), never the router account that writes; Storage Blob Data Contributor on `tfstate-home` |
| `sp-tf-home` | apply of `iac-home`, the certificate renewal included | `iac-home`: `ci.yml` job `apply` | `AZURE_CLIENT_ID_APPLY` | `environment:home-prod` | Key Vault Secrets User on `kv-home`, Storage Blob Data Contributor on `tfstate-home` |
| `sp-tf-identity-plan` | plan of `iac-identity`: pull request, push, weekly drift | `iac-identity`: `ci.yml`, `drift.yml` | `AZURE_CLIENT_ID_PLAN` | `pull_request`, `ref:refs/heads/main` | Graph `AccessReview.Read.All`, `Application.Read.All`, `Directory.Read.All`, `Policy.Read.All`, `RoleManagement.Read.Directory`; Key Vault Reader and Reader on `kv-security` (public parts of certificates); Storage Blob Data Contributor on `tfstate-identity` |
| `sp-tf-identity` | apply of `iac-identity` (Tier-0 in Entra) | `iac-identity`: `ci.yml` job `apply` | `AZURE_CLIENT_ID_APPLY` | `environment:identity-prod` | Graph `*.ReadWrite` of applications, grants, roles, groups and the Entra policies but Conditional Access (`AccessReview`, `AppRoleAssignment`, `Application`, `DelegatedPermissionGrant`, `Directory`, `Group`, `GroupMember`, `RoleManagement.ReadWrite.Directory`, `Policy.ReadWrite.*`), `Policy.Read.All`, `User.Read.All`; no Entra role; Key Vault Reader and Reader on `kv-security`; Storage Blob Data Contributor on `tfstate-identity` |
| `sp-tf-conditional-access-plan` | plan of `iac-conditional-access`: pull request, push, weekly drift | `iac-conditional-access`: `ci.yml`, `drift.yml` | `AZURE_CLIENT_ID_PLAN` | `pull_request`, `ref:refs/heads/main` | Graph `Application.Read.All`, `Group.Read.All`, `Policy.Read.All`, `RoleManagement.Read.Directory`; Storage Blob Data Contributor on `tfstate-conditional-access` |
| `sp-tf-conditional-access` | apply of `iac-conditional-access` | `iac-conditional-access`: `ci.yml` job `apply` | `AZURE_CLIENT_ID_APPLY` | `environment:conditional-access-prod` | Graph as the plan, plus `Policy.ReadWrite.ConditionalAccess`; no Entra role; Storage Blob Data Contributor on `tfstate-conditional-access` |
| `sp-tf-intune-plan` | plan of `iac-intune`: pull request, push, weekly drift | `iac-intune`: `ci.yml`, `drift.yml` | `AZURE_CLIENT_ID_PLAN` | `pull_request`, `ref:refs/heads/main` | Graph `DeviceManagementApps.Read.All`, `DeviceManagementConfiguration.Read.All`, `DeviceManagementRBAC.Read.All`, `DeviceManagementServiceConfig.Read.All`, `Group.Read.All`; Storage Blob Data Contributor on `tfstate-intune` |
| `sp-tf-intune` | apply of `iac-intune` | `iac-intune`: `ci.yml` job `apply` | `AZURE_CLIENT_ID_APPLY` | `environment:intune-prod` | Graph `DeviceManagementApps.ReadWrite.All`, `DeviceManagementConfiguration.ReadWrite.All`, `DeviceManagementServiceConfig.ReadWrite.All`, `DeviceManagementRBAC.Read.All`, `Group.Read.All` (never managed devices); no Entra role; Storage Blob Data Contributor on `tfstate-intune` |
| `sp-tf-exchange-plan` | plan of `iac-exchange`: pull request, push, weekly drift | `iac-exchange`: `ci.yml`, `drift.yml` | `AZURE_CLIENT_ID_PLAN` | `pull_request`, `ref:refs/heads/main` | Global Reader, Security Reader; Exchange `Exchange.ManageAsApp`; Graph `Application.Read.All`, `Group.Read.All`; Storage Blob Data Contributor on `tfstate-exchange` |
| `sp-tf-exchange` | apply of `iac-exchange`: Exchange Online and Defender for Office 365 | `iac-exchange`: `ci.yml` job `apply` | `AZURE_CLIENT_ID_APPLY` | `environment:exchange-prod` | Exchange Administrator; Exchange `Exchange.ManageAsApp`; Graph `Application.Read.All`, `Group.Read.All`; Storage Blob Data Contributor on `tfstate-exchange` |
| `sp-tf-purview-plan` | plan of `iac-purview`: pull request, push, weekly drift | `iac-purview`: `ci.yml`, `drift.yml` | `AZURE_CLIENT_ID_PLAN` | `pull_request`, `ref:refs/heads/main` | Global Reader; Exchange `Exchange.ManageAsApp` (Security & Compliance PowerShell app-only); Graph `Group.Read.All`; Storage Blob Data Contributor on `tfstate-purview` |
| `sp-tf-purview` | apply of `iac-purview`: sensitivity labels and their policies, DLP, retention and alert policies | `iac-purview`: `ci.yml` job `apply` | `AZURE_CLIENT_ID_APPLY` | `environment:purview-prod` | Compliance Administrator; Exchange `Exchange.ManageAsApp` (Security & Compliance PowerShell app-only); Graph `Group.Read.All`; Storage Blob Data Contributor on `tfstate-purview` |
| `sp-tf-sentinel-plan` | plan of `iac-sentinel`: pull request, push, weekly drift | `iac-sentinel`: `ci.yml`, `drift.yml` | `AZURE_CLIENT_ID_PLAN` | `pull_request`, `ref:refs/heads/main` | Microsoft Sentinel Reader, Log Analytics Reader and Reader on `rg-security`, Reader on `rg-soar`; Security Reader (Entra diagnostic setting); Graph `Application.Read.All`, `Group.Read.All`, `User.Read.All` (watchlists); Storage Blob Data Contributor on `tfstate-sentinel` |
| `sp-tf-sentinel` | apply of `iac-sentinel` | `iac-sentinel`: `ci.yml` job `apply` | `AZURE_CLIENT_ID_APPLY` | `environment:sentinel-prod` | Microsoft Sentinel Contributor and Log Analytics Contributor on `rg-security`, Logic App Contributor on `rg-soar`, Security Reader on `sub-mgmt-prod`, `sub-osvc-prod`, `sub-sro-prod` (Defender for Cloud connectors); Security Administrator (Entra diagnostic setting, connectors); Graph as the plan; Storage Blob Data Contributor on `tfstate-sentinel` |
| `sp-ci-major-tag` | moves `v2` to a published release | `ci`: `major-tag.yml` | `AZURE_CLIENT_ID_MAJOR_TAG` | `environment:major-tag` | Key Vault Secrets User on `kv-githubbot` (signs in as `bearclave-bot`) |
| `sp-ci-deps` | dependency and pin bumps | `ci`: `deps.yml`, `ci-pins.yml` | `AZURE_CLIENT_ID_DEPS` | `environment:deps` | Key Vault Secrets User on `kv-githubbot` |
| `sp-project-sync` | upkeep of the organisation's issues: Projects and the housekeeping report | `ci`: `project-sync.yml`, `housekeeping.yml`; `ops-tools`: `project-sync.yml` (variable `AZURE_CLIENT_ID`) | `AZURE_CLIENT_ID_PROJECT_SYNC` | `environment:project-sync`; `ops-tools`: `ref:refs/heads/main` | Key Vault Secrets User on `kv-githubbot` |
| `sp-swa-osvc-prod` | deploy of matejhrabalek.cz | `app-web-matejhrabalek`: `ci.yml` (`static-web-app.yml` job `deploy`) | `AZURE_CLIENT_ID` | `environment:prod` | Static Web App Secret Reader on its site; Contributor on its resource group `rg-web` |
| `sp-swa-sro-prod` | deploy of bearclave.cz | `app-web-bearclave`: `ci.yml` (`static-web-app.yml` job `deploy`, from v2.8 with staging `deploy-prod`) | `AZURE_CLIENT_ID` | `environment:prod` | Static Web App Secret Reader on its site; Contributor on its resource group `rg-web` |
| `sp-swa-osvc-staging` | deploy of the staging site and the previews of matejhrabalek.cz (v2.8) | `app-web-matejhrabalek`: `ci.yml` (`static-web-app.yml` jobs `deploy-staging`, `preview`, `preview-cleanup`) | `AZURE_CLIENT_ID_STAGING` | `environment:staging`, `environment:preview` | on the staging SWA only: `Microsoft.Web/staticSites/read`, `listSecrets/action`, `builds/read`, `builds/delete` (the previews) |
| `sp-swa-sro-staging` | the same for bearclave.cz | `app-web-bearclave`: as above | `AZURE_CLIENT_ID_STAGING` | `environment:staging`, `environment:preview` | as above, on its staging SWA |
| `sp-ci-promote-web-osvc`, `sp-ci-promote-web-sro` | promotion of a site to production as `bearclave-promote` (v2.8) | `app-web-matejhrabalek`, `app-web-bearclave`: `promote.yml` | `AZURE_CLIENT_ID_PROMOTE` | `environment:promote`; recommended as a flexible credential that also requires `job_workflow_ref` of a released `ci` `promote.yml` ([applications.md](applications.md#the-app-and-its-key)) | Key Vault Secrets User on `kv-ghpromote-sec-weu-001` only, never on `kv-githubbot` |
| `sp-ci-promote-home` | promotion of `app-home` to production as `bearclave-promote` (v2.9) | `app-home`: `promote.yml` | `AZURE_CLIENT_ID_PROMOTE` | `environment:promote`; recommended as a flexible credential that also requires `job_workflow_ref` of a released `ci` `promote.yml` ([applications.md](applications.md#the-app-and-its-key)) | Key Vault Secrets User on `kv-ghpromote-sec-weu-001` only, never on `kv-githubbot` |

Two known exceptions, both until they are moved:

- `sp-project-sync` keeps its credential on `ref:refs/heads/main` of
  `ops-tools`, whose `project-sync.yml` still runs; it goes when that
  workflow is deleted.
- The transitional credentials of the callers before v2.5 are gone
  (`iac-identity` #9). The variables `AZURE_CLIENT_ID_PLATFORM` of
  `iac-azure` and `AZURE_CLIENT_ID` of `iac-home` are still defined in
  `iac-github` but no workflow reads them; they go with the next change
  there.

## GitHub Apps

| App | Purpose | Used by | Permissions | Key |
|---|---|---|---|---|
| `bearclave-iac-github` | apply of `iac-github` | `iac-github` job `apply` (`sp-tf-github`) | what `iac-github` manages, write ([setup.md](https://github.com/bearclave/iac-github/blob/main/docs/setup.md#2-github-apps)) | `kv-github-sec-weu-001` |
| `bearclave-iac-github-read` | plan and drift of `iac-github` | `iac-github` plan jobs and `drift.yml` (`sp-tf-github-plan`) | the same areas, read; Organization administration write (GitHub needs it to read an organisation ruleset) | `kv-githubro-sec-weu-001` |
| `bearclave-bot` | author of the agents' and the bots' pull requests; the major tag | `bot-gh` (`tools/bot-gh.py`, the owner and agents), `deps.yml`, `ci-pins.yml`, `major-tag.yml`, `project-sync.yml`, `housekeeping.yml` | contents, pull requests, issues, workflows write; organisation Projects write; checks, actions read; bypasses the ruleset `major-tags`. Each workflow narrows its installation token to what it needs | `kv-githubbot-sec-weu-001` |
| `bearclave-promote` | moves `prod` of an application and tags its release | `promote.yml` in `app-home`, `app-web-bearclave`, `app-web-matejhrabalek` (installed only there) | Contents, Workflows write; Metadata read; the only bypass of the `prod` ruleset and a bypass of `v*` tag creation in those repositories | `kv-ghpromote-sec-weu-001` (`github-app-key`, `github-app-id`, `github-app-installation-id`) |
| `bearclave-nas-runner` | registers the ephemeral runners of the NAS in the runner group `nas` | the launcher on the NAS (`app-nas` `deploy/runner/`), not a workflow | Organization: Self-hosted runners write; nothing on any repository. Its token is narrowed to that permission | on the NAS (`/volume1/@runner/app-key.pem`, root only) and in `kv-tech-sec-weu-001` (`github-app-runner-key`), which no workflow reads |
| `claude` (Anthropic) | Claude Code cloud sessions | the repositories of the owner's cloud environments | contents, pull requests write ([setup.md](https://github.com/bearclave/iac-github/blob/main/docs/setup.md#2-github-apps)) | held by Anthropic |

The installations are outside Terraform: `iac-github` `docs/setup.md` (2. GitHub Apps)
says what each may do and how to check it.

## Secret

| Secret | Repositories | What | Key |
|---|---|---|---|
| `MODULES_DEPLOY_KEY` | every Terragrunt repository: `iac-azure`, `iac-github`, `iac-home`, `iac-identity`, `iac-conditional-access`, `iac-intune`, `iac-exchange`, `iac-purview`, `iac-sentinel` | private half of a read-only deploy key of `iac-modules`, one per repository; the workflows check the modules out with it | public half in `iac-github` `repositories/iac-modules`, private half only in the secret |
| `DEPLOY_KEY` | `app-home`, environment secret of `staging` and of `prod` (one key each) | SSH key of the NAS user `ci-deploy`; its forced command deploys `home-app` to that one environment and takes only the image tag (`app-nas` `docs/runner.md`). `container.yml` job `deploy`, on the NAS runner | public half in `/var/services/homes/ci-deploy/.ssh/authorized_keys` on the NAS, private half only in the environment secret |

## Environments

| Environment | Repository | Deployments from | Identity |
|---|---|---|---|
| `azure-prod` | `iac-azure` | `main` | `sp-tf-platform` |
| `github-prod` | `iac-github` | `main` | `sp-tf-github` |
| `home-prod` | `iac-home` | `main` | `sp-tf-home` |
| `identity-prod` | `iac-identity` | `main` | `sp-tf-identity` |
| `conditional-access-prod` | `iac-conditional-access` | `main` | `sp-tf-conditional-access` |
| `intune-prod` | `iac-intune` | `main` | `sp-tf-intune` |
| `exchange-prod` | `iac-exchange` | `main` | `sp-tf-exchange` |
| `purview-prod` | `iac-purview` | `main` | `sp-tf-purview` |
| `sentinel-prod` | `iac-sentinel` | `main` | `sp-tf-sentinel` |
| `prod` | `app-web-bearclave`, `app-web-matejhrabalek` | `prod` | `sp-swa-*-prod` |
| `staging` | `app-web-bearclave`, `app-web-matejhrabalek` | `staging` | `sp-swa-*-staging` |
| `preview` | `app-web-bearclave`, `app-web-matejhrabalek` | `refs/pull/*/merge` (pull requests) | `sp-swa-*-staging` |
| `promote` | `app-home`, `app-web-bearclave`, `app-web-matejhrabalek` | `staging` | `sp-ci-promote-*` (key of `bearclave-promote`) |
| `staging`, `prod` | `app-home` | `staging`, `prod` | none in Azure: the environment secret `DEPLOY_KEY` |
| `major-tag` | `ci` | `main`, tags `v*.*.*` | `sp-ci-major-tag` |
| `deps` | `ci` | `main`, tags `v*.*.*` (`ci-pins.yml` runs on the release) | `sp-ci-deps` |
| `project-sync` | `ci` | `main` | `sp-project-sync` |

The environments, their branch policies and the variables are defined in
`iac-github` (`infrastructure/repositories/<name>`).

## Rotation

| What | How often | How |
|---|---|---|
| Federated credentials | never: nothing secret | a new subject (renamed repository, environment) is a pull request in `iac-identity` |
| GitHub App keys (`bearclave-promote` included) | once a year, or at once on suspicion | GitHub, the App's settings, Private keys, Generate; `az keyvault secret set --vault-name <vault> --name github-app-key --file <pem>`; delete the old key in GitHub and the file (created by `tools/iac-github/github-apps.py` of the know-how repository) |
| `MODULES_DEPLOY_KEY` | once a year, or at once on suspicion | `iac-github` [docs/deploy-keys.md](https://github.com/bearclave/iac-github/blob/main/docs/deploy-keys.md): new pair, the public half in code, `gh secret set` from stdin |
| `DEPLOY_KEY` of `app-home` | once a year, or at once on suspicion | `app-nas` [docs/runner.md](https://github.com/bearclave/app-nas/blob/main/docs/runner.md#rotation): new pair, the public half into `authorized_keys` on the NAS, `gh secret set --env <environment>` from stdin, the old line removed |
| Key of `bearclave-nas-runner` | once a year, or at once on suspicion | as the other App keys, into `kv-tech-sec-weu-001` and the NAS (`app-nas` `docs/runner.md`) |

Check what is federated where:

```bash
az ad app federated-credential list --id <application id> --query '[].{name:name, subject:subject}' -o table
gh api /orgs/bearclave/installations --jq '.installations[] | {app_slug, repository_selection}'
```
