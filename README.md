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
| `pr.yml` | `pr` | every repository | title, branch, description sections (people and Apps), labels, assignee; the root layout; to-dos with their issue and relative links; markdownlint ([checks.md](docs/checks.md)) |
| `checks.yml` | `pipeline` | `governance`, `handbook`, `ops-tools`, `app-office-templates`, `vs-code-extensions`, `.github` | one job: ESLint where there is JavaScript, an optional command of the repository; the gate |
| `terragrunt.yml` | `pipeline` | `iac-azure`, `iac-github` | conventions, Sentinel lint, validate next to the plan of the affected units (read-only identity) with trivy and a plan comment, `destroy / approved`, apply from `main` in the environment, its verification, an apply comment, gate ([terragrunt.md](docs/terragrunt.md)) |
| `terragrunt-home.yml` | `pipeline` | `iac-home` | the jobs of `terragrunt.yml` through WireGuard, with the certificate renewal on schedule |
| `destroy-review.yml` | `review` | `iac-azure`, `iac-github`, `iac-home` | on a submitted approval, re-runs the failed `destroy / approved` check |
| `terragrunt-drift.yml` | `drift` | `iac-azure`, `iac-github` | weekly plan of the whole tree as the read-only identity, trivy, failed check blocks, analytic rules outside the code |
| `terraform-modules.yml` | `pipeline` | `iac-modules` | fmt, validate per module, tflint, trivy config, conventions |
| `m365-dsc.yml`, `m365-drift.yml` | `pipeline`, `drift` | `iac-m365` | Microsoft365DSC: plan, apply, weekly drift (to be retired) |
| `static-web-app.yml` | `pipeline` | `app-web-*` | site checks, ESLint, the site's own checks, deploy in the `environment` when the content changed |
| `container.yml` | `pipeline` | `app-home` | lint and tests in the image; push to GHCR from `main` (`:main`) and from a release (`:vX.Y.Z`) |
| `content-sentinel.yml` | `pipeline` | `content-sentinel` | Sentinel lint, generated views up to date |
| `release.yml` | `release` | versioned repositories, by hand (no caller yet) | the next `vX.Y.Z` and its notes, and the commands of a signed release; publishes nothing |
| `conventions.yml`, `sentinel-lint.yml` | - | v1 callers only | the same checks as standalone workflows |

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
repositories in the 'bearclave' organization". The `template-iac-*`
repositories (to be archived) cannot reach it and carry a local copy written
by `tools/export-for-templates.py`; `tools/sync-templates.py` keeps the rest
they share with their live repository.

## Layout

```text
templates/            the workflows, with @@include markers; _fragments/ shared pieces
.github/workflows/    generated from templates/ by tools/build.py (do not edit), and
                      this repository's own: ci, pr-check, deps, ci-pins, major-tag,
                      project-sync, housekeeping
src/                  the scripts the workflows carry or run; terragrunt/, m365/;
                      layout.yaml (managed copy from governance), markdownlint.yaml
tests/                unit tests of src/, mirroring it
tools/                build.py, export-for-templates.py, sync-templates.py,
                      sanitise-plan.py, move-major-tag.sh, bot-gh.py
docs/                 identities.md, terragrunt.md, checks.md, organisation-workflows.md
```

- [docs/identities.md](docs/identities.md) - every identity the workflows and Apps use
- [docs/terragrunt.md](docs/terragrunt.md) - the Terragrunt pipeline: jobs, identities, verification, comments
- [docs/checks.md](docs/checks.md) - pr / check, conventions, exceptions, Sentinel, site checks, ESLint
- [docs/organisation-workflows.md](docs/organisation-workflows.md) - deps, pins, major tag, Projects, housekeeping, bot-gh

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
git -C ~/Repositories/Bearclave.GitHub/platform/ci novy <type>/<slug>
# edit templates/, templates/_fragments/ or src/ - never the generated workflows
tools/build.py
python3 -m unittest discover -s tests
python3 src/pr_check.py --self-test && python3 src/layout_check.py --self-test
tools/build.py --check
actionlint .github/workflows/*.yml
zizmor --persona=regular --min-severity=medium .github/workflows
```

A pull request runs `ci.yml` and `pr-check.yml` from its own branch, so the
changed workflows are tried on this repository first: `security / scan`,
`pr / check`, `pipeline / gate` (the tests, Pester, `build.py --check`), the
conventions and Sentinel self-tests.

**Versions.** Semantic versioning
([versioning.md](https://github.com/bearclave/governance/blob/main/standards/versioning.md)):
every release is a signed tag `v2.Y.Z` and a GitHub release; the major tag
`v2` moves to the newest. Callers use `@v2`; repositories that touch
production pin `@v2.Y.Z` and get the bump as a pull request (`ci-pins.yml`).
A change that would break callers (a new required input, a renamed job and
with it the check name) is a major version. Everything the workflows use is
pinned to a commit (`sha_pinning_required`); the downloaded tools are release
binaries checked against a sha256 - the security tools in the `env` of
`security.yml`, the markdownlint image by its digest in `pr.yml`, the
pipelines' tools in the caller's `.github/tool-versions.env`:

```bash
gh api repos/gitleaks/gitleaks/releases/tags/v8.30.1 \
  --jq '.assets[] | select(.name | test("linux_x64")) | .digest'
```

**Releasing.** This repository releases by hand, because its tags are signed
(`release.yml` works out the same version and notes):

1. Change the workflow in a pull request; merge it once its own run is green.
2. Tag and publish:

   ```bash
   git switch main && git pull --ff-only
   git tag -s v2.5.0 -m v2.5.0 && git push origin v2.5.0
   gh release create v2.5.0 --verify-tag --generate-notes
   ```

3. Publishing runs `major-tag.yml` (moves `v2` as `bearclave-bot`) and
   `ci-pins.yml` (a pull request in every repository that pins an older
   `v2.Y.Z`). Check them with `gh run list --workflow major-tag.yml --limit 1`.
   Fallback and the first tag of a new major: `tools/move-major-tag.sh
   v2.5.0` - the one force push an agent may run.
4. Rerun the pipeline in one caller to see it there too.

`v1` stays where it is for anything that still calls it; it gets no changes.

## Pipeline

This repository calls its own workflows (`ci.yml`): `security.yml`,
`checks.yml` with the tests and `build.py --check` as its command,
`conventions.yml` and `sentinel-lint.yml` over the fixtures in
`tests/fixtures/`, and `pr.yml` (`pr-check.yml`).

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
- The configs a workflow reads in the caller are listed in
  [checks.md](docs/checks.md#paths-the-workflows-read-in-the-caller).

## Gotchas

- [A skipped job passes a required check](https://github.com/bearclave/handbook/blob/main/gotchas/2026-10-02-skipped-job-passes-required-check.md) - why only the gate is required
- [A paths filter and a required check](https://github.com/bearclave/handbook/blob/main/gotchas/2026-10-02-required-check-paths-filter.md)
- [`${{` in a workflow heredoc](https://github.com/bearclave/handbook/blob/main/gotchas/2026-10-02-dollar-brace-in-workflow-heredoc.md) - what build.py refuses
- [zizmor and a private reusable workflow](https://github.com/bearclave/handbook/blob/main/gotchas/2026-10-02-zizmor-ref-confusion-private-reusable.md)
- [Terragrunt destroy plan flags](https://github.com/bearclave/handbook/blob/main/gotchas/2026-10-02-terragrunt-destroy-plan-flags.md), [trivy over a plan](https://github.com/bearclave/handbook/blob/main/gotchas/2026-10-02-trivy-plan-target-main-tf.md)

## Repository settings

Branch protection, required checks, merge settings and labels are defined in
[`bearclave/iac-github`](https://github.com/bearclave/iac-github)
(`repositories/ci`). Engineering standards:
[`bearclave/governance`](https://github.com/bearclave/governance/tree/main/standards).
