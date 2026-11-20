# Identities of the workflows

Every identity a GitHub workflow or GitHub App of `bearclave` uses: what it
is for, where it is federated, what it may do, where its key is and how it is
rotated. Identities of the tenant that no workflow uses (users, the NAS, the
Mac routines) are documented in `bearclave/iac-azure`.

The identities are code: the Entra applications in `iac-azure`
`tenant/applications`, their federated credentials in
`tenant/federated-credentials`, their roles in the units of the scopes they
reach; the GitHub variables and environments in `iac-github`. This page is
the map; the code is the truth.

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

Target state of the audit of 19. 11. 2026 (`ci` v2.5). The GitHub variable is
a repository variable of the repository in the second column.

| Identity | Purpose | Repository, workflow | Variable | Federated subject | Permissions |
|---|---|---|---|---|---|
| `sp-tf-plan` | plan of `iac-azure`: pull request, push, weekly drift | `iac-azure`: `ci.yml`, `drift.yml` | `AZURE_CLIENT_ID_PLAN` | `pull_request`, `ref:refs/heads/main` | Reader on MG `corp`, Global Reader, Storage Blob Data Contributor on the container `tfstate` (the state lock of a plan on `main`), Static Web App Secret Reader on both sites (the provider reads the deployment token) |
| `sp-tf-platform` | apply of `iac-azure` (Tier-0) | `iac-azure`: `ci.yml` job `apply` | `AZURE_CLIENT_ID_APPLY` | `environment:azure-prod` | Owner on MG `corp` and `corp-applications`, Storage Blob Data Contributor on the state account, Privileged Role, Security, Application, User and Groups Administrator |
| `sp-tf-github-plan` | plan of `iac-github`: pull request, push, weekly drift | `iac-github`: `ci.yml`, `drift.yml` | `AZURE_CLIENT_ID_PLAN` | `pull_request`, `ref:refs/heads/main` | Key Vault Secrets User on `kv-githubro`, Storage Blob Data Contributor on `tfstate-github` |
| `sp-tf-github` | apply of `iac-github` | `iac-github`: `ci.yml` job `apply` | `AZURE_CLIENT_ID_APPLY` | `environment:github-prod` | Key Vault Secrets User on `kv-github`, Storage Blob Data Contributor on `tfstate-github` |
| `sp-tf-home-plan` | plan of `iac-home`: pull request, push, schedule | `iac-home`: `ci.yml` (`terragrunt-home.yml`: validate, plan) | `AZURE_CLIENT_ID_PLAN` | `pull_request`, `ref:refs/heads/main` | the `kv-home` secrets a plan needs (router account `tf-plan`, WireGuard peer `ci`), Storage Blob Data Contributor on `tfstate-home` |
| `sp-tf-home` | apply of `iac-home`, the certificate renewal included | `iac-home`: `ci.yml` job `apply` | `AZURE_CLIENT_ID_APPLY` | `environment:home-prod` | Key Vault Secrets User on `kv-home`, Storage Blob Data Contributor on `tfstate-home` |
| `sp-ci-major-tag` | moves `v2` to a published release | `ci`: `major-tag.yml` | `AZURE_CLIENT_ID_MAJOR_TAG` | `environment:major-tag` | Key Vault Secrets User on `kv-githubbot` (signs in as `bearclave-bot`) |
| `sp-ci-deps` | dependency and pin bumps | `ci`: `deps.yml`, `ci-pins.yml` | `AZURE_CLIENT_ID_DEPS` | `environment:deps` | Key Vault Secrets User on `kv-githubbot` |
| `sp-project-sync` | upkeep of the organisation's issues: Projects and the housekeeping report | `ci`: `project-sync.yml`, `housekeeping.yml` | `AZURE_CLIENT_ID_PROJECT_SYNC` | `environment:project-sync` | Key Vault Secrets User on `kv-githubbot` |
| `sp-swa-osvc-prod` | deploy of matejhrabalek.cz | `app-web-matejhrabalek`: `ci.yml` (`static-web-app.yml` job `deploy`) | `AZURE_CLIENT_ID` | `environment:prod` | Static Web App Secret Reader on its site |
| `sp-swa-sro-prod` | deploy of bearclave.cz | `app-web-bearclave`: `ci.yml` (`static-web-app.yml` job `deploy`, from v2.8 with staging `deploy-prod`) | `AZURE_CLIENT_ID` | `environment:prod` | Static Web App Secret Reader on its site |
| `sp-swa-osvc-staging` | deploy of the staging site and the previews of matejhrabalek.cz (v2.8) | `app-web-matejhrabalek`: `ci.yml` (`static-web-app.yml` jobs `deploy-staging`, `preview`, `preview-cleanup`) | `AZURE_CLIENT_ID_STAGING` | `environment:staging`, `environment:preview` | on the staging SWA only: `Microsoft.Web/staticSites/read`, `listSecrets/action`, `builds/read`, `builds/delete` (the previews) |
| `sp-swa-sro-staging` | the same for bearclave.cz | `app-web-bearclave`: as above | `AZURE_CLIENT_ID_STAGING` | `environment:staging`, `environment:preview` | as above, on its staging SWA |
| `sp-ci-promote-web-osvc`, `sp-ci-promote-web-sro` | promotion of a site to production as `bearclave-promote` (v2.8) | `app-web-matejhrabalek`, `app-web-bearclave`: `promote.yml` | `AZURE_CLIENT_ID_PROMOTE` | `environment:promote`; recommended as a flexible credential that also requires `job_workflow_ref` of a released `ci` `promote.yml` ([applications.md](applications.md#the-app-and-its-key)) | Key Vault Secrets User on `kv-ghpromote-sec-weu-001` only, never on `kv-githubbot` |
| `sp-ci-promote-home` | promotion of `app-home` to production as `bearclave-promote` (v2.9) | `app-home`: `promote.yml` | `AZURE_CLIENT_ID_PROMOTE` | `environment:promote`; recommended as a flexible credential that also requires `job_workflow_ref` of a released `ci` `promote.yml` ([applications.md](applications.md#the-app-and-its-key)) | Key Vault Secrets User on `kv-ghpromote-sec-weu-001` only, never on `kv-githubbot` |
| `sp-dsc-m365` | Microsoft365DSC of `iac-m365` (to be retired, unchanged) | `iac-m365`: `ci.yml`, `drift.yml` | `AZURE_CLIENT_ID` | `pull_request`, `environment:m365-prod`, `ref:refs/heads/main` | Exchange, Intune, Compliance, SharePoint, Teams Administrator, Graph application permissions |

`iac-m365` keeps one identity for plan and apply until the repository is
retired: the known exception to the rules above.

**Until the callers move.** A repository pinned to a `ci` release before
v2.5 still plans `main` (and `iac-home` its pull requests) as the identity
that writes, and the sites deploy without an environment. `iac-azure` keeps
those credentials (`gh-tf-platform-drift-iac-azure`, `gh-tf-github-drift`,
`gh-tf-home-plan`, `gh-tf-home-drift`, `gh-swa-*-prod-app`) and the
`ops-tools` one of `sp-project-sync` until each repository runs v2.5; then
they go.

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
| `MODULES_DEPLOY_KEY` | `iac-azure`, `iac-github`, `iac-home` | private half of a read-only deploy key of `iac-modules`, one per repository; the workflows check the modules out with it | public half in `iac-github` `repositories/iac-modules`, private half only in the secret |
| `DEPLOY_KEY` | `app-home`, environment secret of `staging` and of `prod` (one key each) | SSH key of the NAS user `ci-deploy`; its forced command deploys `home-app` to that one environment and takes only the image tag (`app-nas` `docs/runner.md`). `container.yml` job `deploy`, on the NAS runner | public half in `/var/services/homes/ci-deploy/.ssh/authorized_keys` on the NAS, private half only in the environment secret |

## Environments

| Environment | Repository | Deployments from | Identity |
|---|---|---|---|
| `azure-prod` | `iac-azure` | `main` | `sp-tf-platform` |
| `github-prod` | `iac-github` | `main` | `sp-tf-github` |
| `home-prod` | `iac-home` | `main` | `sp-tf-home` |
| `m365-prod` | `iac-m365` | `main` | `sp-dsc-m365` |
| `prod` | `app-web-bearclave`, `app-web-matejhrabalek` | `main`; with the environment model (v2.8) `prod` only | `sp-swa-*-prod` |
| `staging` | `app-web-bearclave`, `app-web-matejhrabalek` | `staging` | `sp-swa-*-staging` |
| `preview` | `app-web-bearclave`, `app-web-matejhrabalek` | `refs/pull/*/merge` (pull requests) | `sp-swa-*-staging` |
| `promote` | `app-home`, `app-web-bearclave`, `app-web-matejhrabalek` | `staging` | `sp-ci-promote-*` (key of `bearclave-promote`) |
| `staging`, `prod` | `app-home` | `staging`, `prod` | none in Azure: the environment secret `DEPLOY_KEY` |
| `major-tag` | `ci` | `main`, tags `v*.*.*` | `sp-ci-major-tag` |
| `deps` | `ci` | `main`, tags `v*.*.*` (`ci-pins.yml` runs on the release) | `sp-ci-deps` |
| `project-sync` | `ci` | `main` | `sp-project-sync` |

The environments, their branch policies and the variables are defined in
`iac-github` (`repositories/<name>`).

## Rotation

| What | How often | How |
|---|---|---|
| Federated credentials | never: nothing secret | a new subject (renamed repository, environment) is a pull request in `iac-azure` |
| GitHub App keys (`bearclave-promote` included) | once a year, or at once on suspicion | GitHub, the App's settings, Private keys, Generate; `az keyvault secret set --vault-name <vault> --name github-app-key --file <pem>`; delete the old key in GitHub and the file (created by `tools/iac-github/github-apps.py` of the know-how repository) |
| `MODULES_DEPLOY_KEY` | once a year, or at once on suspicion | `iac-github` [docs/deploy-keys.md](https://github.com/bearclave/iac-github/blob/main/docs/deploy-keys.md): new pair, the public half in code, `gh secret set` from stdin |
| `DEPLOY_KEY` of `app-home` | once a year, or at once on suspicion | `app-nas` [docs/runner.md](https://github.com/bearclave/app-nas/blob/main/docs/runner.md#rotation): new pair, the public half into `authorized_keys` on the NAS, `gh secret set --env <environment>` from stdin, the old line removed |
| Key of `bearclave-nas-runner` | once a year, or at once on suspicion | as the other App keys, into `kv-tech-sec-weu-001` and the NAS (`app-nas` `docs/runner.md`) |

Check what is federated where:

```bash
az ad app federated-credential list --id <application id> --query '[].{name:name, subject:subject}' -o table
gh api /orgs/bearclave/installations --jq '.installations[] | {app_slug, repository_selection}'
```
