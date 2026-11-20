# Applications: staging, previews, promote, production

The environment model of the applications (governance
[workflow.md](https://github.com/bearclave/governance/blob/main/standards/workflow.md),
Applications): the default branch `staging` runs in staging, `prod` runs in
production and moves only forward, by `promote.yml` as the GitHub App
`bearclave-promote`. This
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
| push of the tag `vX.Y.Z` (by `promote.yml`, after a green production run) | a container: the image of the commit gets the tag `:vX.Y.Z`; nothing is deployed. A website: no run (its caller has no tag trigger) | none (`GITHUB_TOKEN`, `packages: write`) |

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

**Production from the staging build.** On `prod` the job takes the newest
successful run of the caller's pipeline for the same commit on `staging`
(push or manual), refuses unless its job `deploy-staging` succeeded, and
downloads its artifact (`actions/download-artifact` with `run-id` and the
run's token, `actions: read`). `promote.py` picks the run by the same rule,
so both see the same build; only a newer successful staging run of the same
commit (a manual rerun in between) could differ, and that is the same
commit, deployed to staging and green. A commit status naming the run would
pin it exactly, but reading it needs `statuses: read`, which a caller
pinned before this release does not grant - and a reusable workflow whose
job asks for more than the caller grants fails as a whole, skipped job or
not. A commit whose build has expired cannot be deployed to production;
`promote.yml` refuses it first. Promote a newer commit of `staging` instead
(it contains the older one).

**Deployments never overlap.** `deploy-staging` and `deploy-prod` have their
own concurrency groups (`deploy-staging-<repository>`,
`deploy-prod-<repository>`), queued, never cancelled.

**noindex.** Staging and the previews are public on `*.azurestaticapps.net`.
The deploy job adds `X-Robots-Tag: noindex, nofollow` to `globalHeaders` of
the deployed copy of `staticwebapp.config.json`; the repository never carries
it: the site job fails a config with an `X-Robots-Tag` (global or in a
route) before anything is built, and the production job checks it again. The
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

## container.yml

The same contract for a container (`app-home`), with `environments: true`.
The build is the image `:sha-<commit>` in GHCR, pushed by the staging run;
there is no artifact, so the caller of `promote.yml` passes `artifact: ""`,
`staging-job: deploy` and `prod-job: deploy`.

| Event | Jobs | Does |
|---|---|---|
| push to `staging`, manual run on `staging` | `build`, `publish`, `deploy` | lint and tests, the image as `:staging` and `:sha-<commit>`, deployment to `staging`, smoke test |
| push to `prod`, manual run on `prod` | `prod-image`, `deploy` | `:sha-<commit>` must exist (its digest goes to the summary); deployment of `:sha-<commit>` to `prod`, smoke test; nothing is built |
| push of the tag `vX.Y.Z` (manual run on the tag: the same) | `version-tag` | the tagged commit must be on `prod`; `:sha-<commit>` is copied to `:vX.Y.Z` in the registry by digest (`docker buildx imagetools create`); an existing `:vX.Y.Z` of another digest fails, it never moves; nothing is built or deployed |

**Why production does not wait for the version.** `promote.py` tags
`vX.Y.Z` only after the production run is green (a version is what
production runs). Production therefore deploys `:sha-<commit>`, the image
staging ran, and the image gets its version when the tag is pushed: the
caller's `ci.yml` has `push: tags: ['v*']`. The tag is pushed with the App's
installation token, so its push starts a run (a push with `GITHUB_TOKEN`
would not). Tagging the image in `promote.py` instead would need `packages:
write` for the App and for the promote job, which neither has. The app
reports `version` = `sha-<commit>` in production, as in staging; the release
names the version.

If the tag run fails, production is not affected: run `ci.yml` by hand on
the tag `vX.Y.Z` (Actions, Run workflow, "Use workflow from" the tag). A
manual rerun of a staging run of the same commit rebuilds and overwrites
`:sha-<commit>` with an image of the same commit; if that happens between
the production run and the tag, `:vX.Y.Z` names the rebuild. That is
accepted: the same commit, deployed to staging and green.

## promote.yml

Runs by hand from `staging` (`promote.yml` of the application,
`workflow_dispatch`), in the environment `promote`, one at a time per
repository (job concurrency `promote-<repository>`). `src/promote.py`:

1. The commit is `sha` (a full SHA, checked first) or the head of
   `staging`; it is on `staging`, and `prod` is its ancestor (a fast-forward).
2. The newest successful run of `workflow` (`ci.yml`) for the commit on
   `staging` (push or manual) has a successful job `staging-job`
   (`deploy-staging`): a run that did not deploy staging, or a run of a tag
   called `staging`, does not count. Its build `artifact` (`site`) is there
   and does not expire within the hour.
3. The version: `version`, or the next one from the pull request titles since
   the last tag (`src/release_version.py`); only `docs`, `test`, `ci`,
   `chore` since the last tag is refused unless a version is given.
4. As `bearclave-promote` (an installation token for this repository only:
   `contents`, `workflows` write; masked in the log): `prod` is created or
   fast-forwarded (`force: false`).
5. It waits (`wait-minutes`, 20) for the production run of the commit. Only
   when that run is green does it create the annotated tag `vX.Y.Z` and the
   release with generated notes: a release is what production runs. A red
   or unfinished production run tags nothing; promote again once it is
   green (prod is already at the commit, so only the tag and release follow).
   For a container, the tag's push makes `container.yml` tag the image
   `:vX.Y.Z` ([container.yml](#containeryml)).

Each step is skipped when it is done already, so a rerun completes the
promotion; a given `version` already on the commit is accepted. `dry-run`
checks and shows the plan without signing in.

### The App and its key

`promote.py` signs in as the GitHub App **`bearclave-promote`**, not as
`bearclave-bot`: the bot's key opens every repository and moves the `v2`
tag of this repository, and an environment of an application must not
reach it. `bearclave-promote` has the repository permissions Contents and
Workflows (write) and Metadata (read), nothing else, and is installed only
on `app-home`, `app-web-bearclave` and `app-web-matejhrabalek`. Its key, App ID and installation ID are the secrets
`github-app-key`, `github-app-id`, `github-app-installation-id` of the Key
Vault `kv-ghpromote-sec-weu-001`, a vault of its own (input `vault`), which
only the promote identities read (Key Vault Secrets User on that vault).
The App is created by the owner (`promote-1-github-app.sh`, the procedure
in [`iac-github` docs/setup.md](https://github.com/bearclave/iac-github/blob/main/docs/setup.md#2-github-apps)).

**Binding the identity to this workflow (recommended hardening).** The
federated credential on `environment:promote` lets any job of the
application in that environment sign in. An Entra flexible federated
credential (preview) can require, besides the subject and the repository
ID, that the job runs a released `promote.yml` of `ci`:

```text
claims['sub'] eq 'repo:bearclave@328058774/<repository>@<repository id>:environment:promote'
  and claims['repository_id'] eq '<repository id>'
  and claims['job_workflow_ref'] matches 'bearclave*/ci*/.github/workflows/promote.yml@refs/tags/v*'
```

`job_workflow_ref` is the reusable workflow and its ref, so the wildcard on
the tag covers every exact pin (`@v2.8.0`, `@v2.9.1`) and the credential is
never re-created for a release; only the owner creates `v*` tags in `ci`.
Customising the OIDC subject with `job_workflow_ref` instead would change
the subject at every pin bump and need a new credential each time, so it is
not used. Use the flexible credential when `iac-identity` can write
`claimsMatchingExpression` (Microsoft Graph beta); until then the plain
subject, the narrow App and the branch policy of `promote` are the controls.

## Rollback

`prod` never moves backwards and an old build is never redeployed by hand:
the rollback is a `revert:` pull request into `staging`, its staging
deployment and smoke test, then `promote.yml` (a patch release). Everything
else merged into `staging` since goes with it; revert that too or wait with
the merge. A failed production deployment of a good commit (a transient
error) is a manual run of `ci.yml` on `prod`, which deploys the same build
again.

## Accepted risk: previews

The previews use the staging identity, and the deployment token of the
staging SWA deploys any of its environments, the staging site included: a
pull request can overwrite staging until the next push to `staging`. That is
accepted: production never takes what staging serves (it deploys the build
artifact of the staging run), only the owner pushes, and a separate SWA for
previews would need a second resource and identity for no gain in
production. The environment `preview` admits runs of pull requests through
the deployment branch rule `refs/pull/*/merge`, which GitHub matches against
`GITHUB_REF` of a `pull_request` run (docs.github.com, Deployments and
environments: "Adding another branch rule for `refs/pull/*/merge` would also
allow workflows triggered by `pull_request` events").

## What the repository needs

Set in `iac-github` and `iac-azure` (identities in [identities.md](identities.md)):

| What | Value |
|---|---|
| Default branch | `staging` |
| Environments | `staging` (deployment branch `staging`), `preview` (`refs/pull/*/merge`), `prod` (`prod`), `promote` (`staging`) |
| Repository variables | `AZURE_CLIENT_ID` (production deploy), `AZURE_CLIENT_ID_STAGING` (staging and previews), `AZURE_CLIENT_ID_PROMOTE`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`, `STAGING_SWA_NAME`, `STAGING_SWA_RESOURCE_GROUP`, `STAGING_URL` |
| Ruleset on `prod` | creation, update, deletion and non-fast-forward restricted; the only bypass is `bearclave-promote` (no owner bypass) |
| Tag creation | `bearclave-promote` may create `v*.*.*` in the applications (the owner-only `version-tags-creation` stays for every other repository) |
| No tag `staging` or `prod` | a tag ruleset on `refs/tags/staging` and `refs/tags/prod` (creation restricted, no bypass), so that no ref is ambiguous with the branches |
