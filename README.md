# ci

The shared CI of the `bearclave` organisation: every pipeline as a reusable
workflow, called by a short caller in each repository, and the workflows that
look after the whole organisation (dependency bumps, Projects, housekeeping).

## How it fits

```text
governance ──(sync.py: callers, layout.yaml)──> every repository ──(uses: ...@v2 / @v2.Y.Z)──> ci
ci ──(bearclave-bot: deps, pins, Projects, housekeeping)──> every repository
iac-azure: the identities ci signs in with      iac-github: environments, variables, rulesets
```

A repository holds only callers (templates in governance
`standards/templates/workflows/`); the caller owns the triggers, the
`concurrency` group and what it passes (`with:` for variables, `secrets:` by
name, never `secrets: inherit`). The header of each workflow shows its caller.

| Workflow | Caller job | Used by | What it does |
|---|---|---|---|
| `security.yml` | `security` | every repository | gitleaks over the whole history, actionlint, zizmor, shellcheck; review dates of the gitleaks allowlists |
| `pr.yml` | `pr` | every repository | title, branch, description sections (people and Apps), labels, assignee; the root layout; to-dos with their issue and relative links; the YAML of collections against their JSON Schema; markdownlint ([checks.md](docs/checks.md)) |
| `checks.yml` | `pipeline` | `governance`, `knowhow`, `office-templates`, `vs-code-extensions`, `.github` | one job, the gate: ESLint where there is JavaScript, an optional command of the repository |
| `terragrunt.yml` | `pipeline` | `iac-azure`, `iac-github`; `iac-identity`, `iac-conditional-access`, `iac-intune`, `iac-exchange`, `iac-purview`, `iac-sentinel` | select (relevance, conventions, Sentinel lint, units), validate next to the plan of the affected units (read-only identity) with trivy and a plan comment, the approval of destroys (a step of the gate), apply from `main` in the environment, its verification, an apply comment, gate ([terragrunt.md](docs/terragrunt.md)) |
| `terragrunt-home.yml` | `pipeline` | `iac-home` | the jobs of `terragrunt.yml`; plan and apply on the NAS runner in the home network (`network-runner`) or through WireGuard from a GitHub runner, with the certificate renewal on schedule ([runners.md](docs/runners.md)) |
| `destroy-review.yml` | `review` | `iac-azure`, `iac-github`, `iac-home` | on a submitted approval, re-runs the failed gate (its destroy approval step) |
| `terragrunt-drift.yml` | `drift` | the repositories of `terragrunt.yml` | weekly plan of the whole tree as the read-only identity, trivy, failed check blocks, analytic rules outside the code |
| `terraform-modules.yml` | `pipeline` | `terraform-modules` | fmt, validate per module (a pull request: the modules it touches and their callers, all of them when a shared block, a workflow or a tool setting changes; `src/terraform/changed-modules.py`), `terraform test`, tflint, trivy config, README tables (terraform-docs `--output-check`), `docs/catalog.md` (`src/terraform/catalog.py --check`), conventions |
| `m365-dsc.yml`, `m365-drift.yml` | `pipeline`, `drift` | `iac-m365` | Microsoft365DSC: plan, apply, weekly drift; retired, removed in a minor release once the last domain has moved to Terraform and `iac-m365` is archived |
| `static-web-app.yml` | `pipeline` | `app-web-*` | site checks (with SEO), ESLint, the site's own checks; with `staging-swa-name` the environment model: a preview per pull request, the build deployed to staging from `staging` and the same build to production from `prod`, noindex outside production, smoke tests ([applications.md](docs/applications.md)); without it the deploy into `environment` when the content changed |
| `promote.yml` | `promote` | the applications, by a click | a commit of `staging` to production as the App `bearclave-promote`: checks its staging run and build, fast-forwards `prod`, waits for the production run, then tags `vX.Y.Z` and publishes the release ([applications.md](docs/applications.md)) |
| `container.yml` | `pipeline` | `app-home` | ESLint, lint and tests in the image; with `environments`: `staging` pushes `:staging` and `:sha-<commit>` and deploys staging on the NAS, `prod` deploys that image (no rebuild, also on a manual run), the release tag `vX.Y.Z` pushed by `promote.yml` adds `:vX.Y.Z` to it, smoke test of each deployment; without: push from `main` (`:main`) and from a release (`:vX.Y.Z`) |
| `content-sentinel.yml` | `pipeline` | `content-sentinel` | Sentinel lint, generated views up to date |
| `release.yml` | `release` | versioned repositories, by hand (no caller yet) | the next `vX.Y.Z` and its notes, and the commands of a signed release; publishes nothing |

The workflows of this repository itself:

| Workflow | What |
|---|---|
| `ci.yml`, `pr-check.yml` | the workflows above run on this repository from the pull request's branch, with the tests |
| `deps.yml`, `ci-pins.yml`, `major-tag.yml`, `project-sync.yml`, `housekeeping.yml` | the whole organisation, as `bearclave-bot` ([organisation-workflows.md](docs/organisation-workflows.md)) |

The organisation rulesets (in `iac-github`) require three checks on `main`:
**`security / scan`**, **`pr / check`** and **`pipeline / gate`**. Every
pipeline ends with a `gate` job that always runs: a skipped job passes a
required check, so only the gate may be required.

This repository is private; its Actions access is "Accessible from
repositories in the 'bearclave' organization". Another organisation cannot
reach it.

## Layout

```text
templates/            the workflows, with @@include markers; _fragments/ shared pieces
.github/workflows/    generated from templates/ by tools/build.py (do not edit), and
                      this repository's own: ci, pr-check, deps, ci-pins, major-tag,
                      project-sync, housekeeping
src/                  the scripts the workflows carry or run; terragrunt/, terraform/, m365/;
                      layout.yaml (managed copy from governance), markdownlint.yaml
tests/                unit tests of src/, mirroring it
tools/                build.py, sanitise-plan.py, move-major-tag.sh, bot-gh.py, release.sh
docs/                 identities.md, runners.md, terragrunt.md, applications.md,
                      checks.md, organisation-workflows.md, releasing.md
```

- [docs/identities.md](docs/identities.md) - every identity the workflows and Apps use
- [docs/runners.md](docs/runners.md) - which jobs run on GitHub's runners and which on the NAS, and why
- [docs/terragrunt.md](docs/terragrunt.md) - the Terragrunt pipeline: jobs, identities, verification, comments
- [docs/applications.md](docs/applications.md) - the applications: staging, previews, promote, production, rollback
- [docs/checks.md](docs/checks.md) - pr / check, conventions, exceptions, Sentinel, site checks, ESLint
- [docs/organisation-workflows.md](docs/organisation-workflows.md) - deps, pins, major tag, Projects, housekeeping, bot-gh
- [docs/releasing.md](docs/releasing.md) - versions, pins of the tools, releasing and the major tag

**How a script gets into a workflow.** A reusable workflow runs in the
calling repository, whose token cannot read this private repository, so a
script travels inside the workflow: `templates/<name>.yml` has a line
`@@include src/<script>@@` inside a heredoc, and `tools/build.py` writes
`.github/workflows/<name>.yml` with the script in its place.
`@@include templates/_fragments/<name>.yml@@` puts a shared piece of workflow
there the same way; `terragrunt.yml` and `terragrunt-home.yml` share all their
jobs (`_fragments/terragrunt-jobs.yml`). A script reads the repository it runs
for from the working directory, never from its own location.

## How to make a change

```bash
git -C ~/Repositories/Bearclave.GitHub/bearclave-infrastructure/ci novy <type>/<slug>
# edit templates/, templates/_fragments/ or src/ - never the generated workflows
tools/build.py
python3 -m unittest discover -s tests
python3 src/pr_check.py --self-test && python3 src/layout_check.py --self-test && python3 src/schema_check.py --self-test
tools/build.py --check
actionlint .github/workflows/*.yml
zizmor --persona=regular --min-severity=medium .github/workflows
```

A pull request runs `ci.yml` and `pr-check.yml` from its own branch, so the
changed workflows are tried on this repository first: `security / scan`,
`pr / check`, `pipeline / gate` (the tests, Pester, `build.py --check`), the
conventions and Sentinel self-tests.

Versions, pins and how a release is made:
[docs/releasing.md](docs/releasing.md).

**Releasing any versioned repository.** The owner runs
`tools/release.sh <repo-path> [vX.Y.Z]` from this clone against a clone of the
repository. It refuses unless the checkout is a clean `main` equal to
`origin/main`, while an open pull request has auto-merge on or is approved
and mergeable (`--force-open`), when nothing since the last tag calls for a
release, and when the given version differs from the one the titles give
(`--force-version`); the version comes from `src/release_version.py`. It
shows the titles and the version, asks for the version to be typed, then
signs and pushes the tag and publishes the release with generated notes.
`--dry-run` stops before the tag. An agent never runs it without
`--dry-run`.

## Pipeline

This repository calls its own workflows (`ci.yml`): `security.yml`,
`checks.yml` with the tests, `build.py --check` and the conventions and
Sentinel lint over the fixtures in `tests/fixtures/` as its command, and
`pr.yml` (`pr-check.yml`).

| Trigger | What runs | Identity |
|---|---|---|
| Pull request into `main` | the above, from the pull request's branch | the workflow token |
| Monday | `ci.yml` (new gitleaks rules over the history), `deps.yml` actions, `housekeeping.yml` | the workflow token; `sp-ci-deps`, `sp-project-sync` |
| Daily | `project-sync.yml` | `sp-project-sync` |
| 2nd of the month | `deps.yml` tools and providers | `sp-ci-deps` |
| Release published | `major-tag.yml`, `ci-pins.yml` | `sp-ci-major-tag`, `sp-ci-deps` |

Nothing is deployed by a merge: the callers pick a change up when `v2` moves
or their pin is bumped.

## Conventions

- Edit `templates/` and `src/`, never the generated workflows; `src/layout.yaml`
  is a managed copy (`governance/tools/sync.py ci`).
- A script needs only the Python standard library and the tools of the
  runner image (governance `toolchain.md`); its tests are in `tests/`.
- A workflow step that runs a large script gets a step of its own: actionlint
  deadlocks on a `run:` of more than about 64 KB.
- A generated workflow stays under 512 KiB: GitHub reads every file in
  `.github/workflows` on a push to this repository and fails a larger one as
  "a workflow file issue" (#48), so `build.py` refuses it. A job includes only
  the script fragments it runs (`scripts-select.yml`, `scripts-plan.yml`,
  `scripts-vpn.yml`, `scripts-verify.yml`); `scripts-terragrunt.yml` has them
  all, for `terragrunt-drift.yml`.
- The configs a workflow reads in the caller are listed in
  [checks.md](docs/checks.md#paths-the-workflows-read-in-the-caller).

## Gotchas

- [A skipped job passes a required check](https://github.com/bearclave/knowhow/blob/main/gotchas/2026-10-02-skipped-job-passes-required-check.md) - why only the gate is required
- [A paths filter and a required check](https://github.com/bearclave/knowhow/blob/main/gotchas/2026-10-02-required-check-paths-filter.md)
- [`${{` in a workflow heredoc](https://github.com/bearclave/knowhow/blob/main/gotchas/2026-10-02-dollar-brace-in-workflow-heredoc.md) - what build.py refuses
- [zizmor and a private reusable workflow](https://github.com/bearclave/knowhow/blob/main/gotchas/2026-10-02-zizmor-ref-confusion-private-reusable.md)
- [Terragrunt destroy plan flags](https://github.com/bearclave/knowhow/blob/main/gotchas/2026-10-02-terragrunt-destroy-plan-flags.md), [trivy over a plan](https://github.com/bearclave/knowhow/blob/main/gotchas/2026-10-02-trivy-plan-target-main-tf.md)

## Repository settings

Branch protection, required checks, merge settings and labels are defined in
[`bearclave/iac-github`](https://github.com/bearclave/iac-github)
(`repositories/ci`). Engineering standards:
[`bearclave/governance`](https://github.com/bearclave/governance/tree/main/standards).
