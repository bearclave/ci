# Applications: staging, previews, promote, production

The environment model of the applications (governance
[workflow.md](https://github.com/bearclave/governance/blob/main/standards/workflow.md),
Applications): the default branch `staging` runs in staging, `prod` runs in
production and moves only forward, by `promote.yml` as `bearclave-bot`. This
page is how `static-web-app.yml` and `promote.yml` implement it; the
container side (`container.yml`, `app-home`) follows the same contract.

## The contract of an application's `ci.yml`

| Event | Does | Environment, identity |
|---|---|---|
| pull request into `staging` | checks, tests, the build; a website gets a preview | `preview` (websites), staging identity |
| pull request closed | the preview is deleted | `preview` |
| push to `staging` | checks, the build once, deployment to staging, smoke test | `staging`, staging identity |
| push to `prod` (only the fast-forward of `promote.yml`) | the build of the staging run of the same commit to production, smoke test; nothing is built | `prod`, production identity |
| manual run on `staging` or `prod` | the same as a push there (a redeployment) | as the push |

## static-web-app.yml

The environment model is on when the caller passes `staging-swa-name`;
without it the workflow behaves as before v2.8 (deploy into `environment`
from the branch pushed, when `deploy-paths` changed).

**The build** is the artifact `site` of the run: `site.tar` (`app/` from
`app-location`, `api/` from `api-location`, `COMMIT` with the SHA) and its
sha256, kept `artifact-retention-days` (30, the organisation's artifact
retention; a pull request's one day). A
static site compiles nothing, so the build is the tree of the commit; it is
still built once and carried, so that production deploys exactly the bytes
staging served. The deploy jobs check the sha256 and that `COMMIT` is the
commit of the run.

**Production from the staging build.** On `prod` the job looks up the newest
run of the caller's pipeline for the same commit on `staging` (push or
manual), refuses unless it succeeded, and downloads its artifact
(`actions/download-artifact` with `run-id` and the run's token, `actions:
read`). A commit whose build has expired cannot be deployed to production;
`promote.yml` refuses it first. Promote a newer commit of `staging` instead
(it contains the older one).

**noindex.** Staging and the previews are public on `*.azurestaticapps.net`.
The deploy job adds `X-Robots-Tag: noindex, nofollow` to `globalHeaders` of
the deployed copy of `staticwebapp.config.json`; the repository never carries
it, and the production job fails if the config has an `X-Robots-Tag`. The
smoke test checks the header: there outside production, absent in it.

**Previews.** A pull request gets the preview environment `pr<number>` of
the staging SWA. The Free plan allows three: when they are taken, the
pull request gets a warning and no preview (staging still checks the
deployment before production). Closing the pull request deletes its preview;
the delete refuses any name but `pr<number>`, because without
`--environment-name` the command deletes the production environment of the
SWA.

**Smoke test** (every deployment): the URL answers 200 within about four
minutes, the noindex header is as above, and when the URL is the site's root
the body is the `index.html` of the build. Staging uses `staging-url`
(empty: the URL the CLI prints), production `url`, a preview the URL the CLI
prints.

Inputs of the environment model, besides those of the production SWA:

| Input | Caller passes | Default |
|---|---|---|
| `staging-swa-name`, `staging-swa-resource-group` | `vars.STAGING_SWA_NAME`, `vars.STAGING_SWA_RESOURCE_GROUP` | - |
| `staging-url` | `vars.STAGING_URL` | the URL of the deployment |
| `staging-client-id` | `vars.AZURE_CLIENT_ID_STAGING` | - |
| `staging-branch`, `prod-branch` | - | `staging`, `prod` |
| `staging-environment`, `preview-environment` | - | `staging`, `preview` (empty: no previews) |
| `preview-limit` | - | 3 |
| `artifact-retention-days` | - | 30 |

## promote.yml

Runs by hand from `staging` (`promote.yml` of the application,
`workflow_dispatch`), in the environment `promote`. `src/promote.py`:

1. The commit is `sha` or the head of `staging`; it is on `staging`, and
   `prod` is its ancestor (a fast-forward).
2. The newest run of `workflow` (`ci.yml`) for the commit on `staging`
   succeeded; with `artifact`, its build is there and does not expire within
   the hour.
3. The version: `version`, or the next one from the pull request titles since
   the last tag (`src/release_version.py`); only `docs`, `test`, `ci`,
   `chore` since the last tag is refused unless a version is given.
4. As `bearclave-bot` (installation token for this repository: `contents`,
   `workflows` write): `prod` is created or fast-forwarded (`force: false`),
   then the annotated tag `vX.Y.Z` and the release with generated notes.

`prod` moves first: a tag marks what reached production. Each step is skipped
when it is done already, so a rerun after a failure completes the
promotion. `dry-run` checks and shows the plan without signing in.

## Rollback

`prod` never moves backwards and an old build is never redeployed by hand:
the rollback is a `revert:` pull request into `staging`, its staging
deployment and smoke test, then `promote.yml` (a patch release). Everything
else merged into `staging` since goes with it; revert that too or wait with
the merge. A failed production deployment of a good commit (a transient
error) is a manual run of `ci.yml` on `prod`, which deploys the same build
again.

## What the repository needs

Set in `iac-github` and `iac-azure` (identities in [identities.md](identities.md)):

| What | Value |
|---|---|
| Default branch | `staging` |
| Environments | `staging` (deployment branch `staging`), `preview` (`refs/pull/*/merge`), `prod` (`prod`), `promote` (`staging`) |
| Repository variables | `AZURE_CLIENT_ID` (production deploy), `AZURE_CLIENT_ID_STAGING` (staging and previews), `AZURE_CLIENT_ID_PROMOTE`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`, `STAGING_SWA_NAME`, `STAGING_SWA_RESOURCE_GROUP`, `STAGING_URL` |
| Ruleset on `prod` | creation, update, deletion and non-fast-forward restricted; bypass only `bearclave-bot` |
| Tag creation | `bearclave-bot` may create `v*.*.*` in the applications (the owner-only `version-tags-creation` stays for every other repository) |
