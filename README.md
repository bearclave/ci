# ci

Shared CI for the `bearclave` organisation. The checks that are the same in
every repository live here once, as reusable workflows, instead of a copy in
each repository that drifts on its own.

## Layout

```text
templates/            the workflows, with @@include markers; _fragments/ shared pieces
.github/workflows/    generated from templates/ by tools/build.py (do not edit);
                      ci.yml, pr-check.yml, major-tag.yml are this repository's own
src/                  the scripts the workflows carry; layout.yaml (managed copy)
tests/                unit tests of src/, mirroring it
tools/                build.py, export-for-templates.py, sync-templates.py
```

## Workflows

Every pipeline of the organisation lives here once; a repository holds only
short callers (templates in `bearclave/governance` `standards/templates/workflows/`).

| Workflow | Caller job | Used by | What it does |
|---|---|---|---|
| `security.yml` | `security` | every repository | gitleaks over the whole history, actionlint, zizmor, shellcheck; review dates of the gitleaks allowlists |
| `pr.yml` | `pr` | every repository | Conventional Commit title (a bot's title over 72 characters only warns), `<type>/<slug>` branch, description sections; sets `type:*`, `breaking`, `size:*` labels and the assignee (`src/pr_check.py`); the root layout (`src/layout_check.py`, below) |
| `checks.yml` | `pipeline` | `governance`, `handbook`, `ops-tools`, `app-office-templates`, `vs-code-extensions`, `.github` | ESLint where there is JavaScript, an optional command of the repository, then the gate |
| `terragrunt.yml` | `pipeline` | `iac-azure`, `iac-github` | conventions, Sentinel lint, validate next to the plan of the affected units with trivy and a plan comment, `destroy / approved` (destroys and replaces need an approval given after the plan), apply from `main`, verification of the apply, an apply comment on the merged pull request, gate |
| `destroy-review.yml` | `review` | `iac-azure`, `iac-github` | on a submitted approval, re-runs the failed `destroy / approved` check of the last pull request run |
| `terragrunt-drift.yml` | `drift` | `iac-azure`, `iac-github` | weekly plan of the whole tree, trivy, failed check blocks, analytic rules outside the code |
| `terragrunt-home.yml` | `pipeline` | `iac-home` | conventions, plan and apply of the router through WireGuard, certificate renewal and drift on schedule |
| `terraform-modules.yml` | `pipeline` | `iac-modules` | fmt, validate per module, tflint, trivy config, conventions |
| `m365-dsc.yml` | `pipeline` | `iac-m365` | schema check, Pester, PSScriptAnalyzer, plan of the affected types, apply |
| `m365-drift.yml` | `drift` | `iac-m365` | weekly plan of the whole tenant, tombstones into a pull request |
| `release.yml` | `release` | versioned repositories (governance `versioning.md`) | manual: the next `vX.Y.Z` from the pull request titles since the last tag and a preview of the notes (`src/release_version.py`); dry run by default, publishing needs a `release-token` |
| `static-web-app.yml` | `pipeline` | `app-web-*` | the common site checks, ESLint, the site's own checks (optional), deploy to the Static Web App when the content changed |
| `container.yml` | `pipeline` | `app-home` | lint and tests in the image, push to GHCR from `main` |
| `content-sentinel.yml` | `pipeline` | `content-sentinel` | Sentinel lint, generated views up to date |
| `housekeeping.yml` | `housekeeping` | repositories that opt in | weekly: branches without a pull request over 30 days and pull requests without an update over 14 days, in one issue of the repository that is updated in place and closes itself (`src/housekeeping.py`); deletes nothing |
| `conventions.yml`, `sentinel-lint.yml` | - | v1 callers only | the same checks as standalone workflows; v2 pipelines carry them as jobs |

The organisation rulesets (in `bearclave/iac-github`) require three checks on
`main`: **`security / scan`**, **`pr / check`** and **`pipeline / gate`**.
Every pipeline ends with a `gate` job that always runs and fails when anything
it needs failed, or - on `main` - when an apply that was due did not happen:
a skipped job passes a required check, so only the gate may be required.

## Calling a workflow

```yaml
jobs:
  security:
    uses: bearclave/ci/.github/workflows/security.yml@v2
    permissions:
      contents: read
  pipeline:
    uses: bearclave/ci/.github/workflows/terraform-modules.yml@v2
    permissions:
      contents: read
```

The caller owns the triggers, the `concurrency` group and what it passes
(`with:` for variables, `secrets:` by name - never `secrets: inherit`). The
header of each workflow shows its caller.

The repository is private; its Actions access is set to "Accessible from
repositories in the 'bearclave' organization", which is what lets the others
call it.

**Template repositories cannot.** `template-iac-*` are copied to customers,
whose organisations cannot reach a private `bearclave` repository, so the
templates call a local copy (`uses: ./.github/workflows/security.yml`). The
copies are written by `tools/export-for-templates.py`, which takes out what belongs
to this organisation (the header, zizmor's allowance for `bearclave/*`):

```bash
tools/export-for-templates.py ../../template/template-iac-modules ../../template/template-iac-terragrunt ../../template/template-iac-m365
tools/export-for-templates.py --check ../../template/template-iac-*     # are the copies current?
```

The templates still carry the v1 pipelines; they move to the v2 workflows when
they are rebuilt.

`tools/sync-templates.py` keeps what else a template shares with its live repository
- the runner of `iac-m365`, the modules, the tool versions - and keeps what is
the tenant (account.hcl, the units, the configuration, the README). Each
template has its rules in the script: what is copied, what is replaced
(`bearclave/iac-modules` -> `contoso/...`), and the edits a template needs on
top. It stops when a file would bring the organisation or its tenant into a
template, and when an edit no longer finds its text because the live file
changed shape. It finds the repositories next to this one or in the group
folders (`iac/`, `template/`):

```bash
tools/sync-templates.py --check ~/Repositories/Bearclave.GitHub   # what is out of date
tools/sync-templates.py ~/Repositories/Bearclave.GitHub template-iac-terragrunt
```

## Layout check

The `check` job of `pr.yml` (the required `pr / check`) also checks the root
of the repository against
[`standards/layout.yaml`](https://github.com/bearclave/governance/blob/main/standards/layout.yaml)
of governance: required and allowed root files, the allowed top-level folders
of the repository's group (name prefix) and pipeline (the shared workflow its
`ci.yml` calls), what `.github/` holds and the names in `docs/`. The YAML
travels in the workflow as `src/layout.yaml`, a copy that
`governance/tools/sync.py ci` writes. Exceptions are in the repository's
`.github/conventions.yaml`, each with a reason:

```yaml
layout:
  exceptions:
    - path: ISSUE_TEMPLATE
      reason: GitHub reads the issue forms from the root of this repository.
```

A layout exception may carry a `review` date too; it is optional there (the
layout is structure), and a date that has passed only warns.

## Paths the workflows read in the caller

The configs moved from the root into `.github/` (governance `layout.md`); the
workflows read the new path and fall back to the old one, so a repository
works before and after its move:

| Config | Path | Before the move |
|---|---|---|
| conventions | `.github/conventions.yaml` | `conventions.yaml` |
| Sentinel lint | `.github/sentinel-lint.yaml` | `sentinel-lint.yaml` |
| trivy exceptions | `.github/trivyignore.yaml` | `.trivyignore.yaml` |
| PSScriptAnalyzer | `.github/pssa.psd1` | `.pssa.psd1` |
| home VPN, shell scripts | `tools/home-vpn.sh`, `tools/*.sh`, `tools/bootstrap/render.sh` | `scripts/`, `bootstrap/` |
| content generators | `tools/*.py` | `_tools/*.py` |

## Plan and apply comments

`terragrunt.yml` writes two comments, built from the plan in JSON without
anything but the scripts (`src/terragrunt/plan-summary.py`, `plan_details.py`,
`apply-summary.py`; tests with sanitised real plans in `tests/terragrunt/`).

**Plan comment**, one per pull request, rewritten on every push (marker
`terragrunt-plan`):

```
## 📋 Plan complete                      ## ❌ Plan failed on an error or a failed check
repo · commit · units · ➕ 1 · ✏️ 2 · 🗑️ 0 · ♻️ 1
### ⚠️ Destroys and replaces             icon | Resource | Name | Unit | Why
### Changes                              icon | Resource | Name | What changes | From | To
📥 Imported without a change: n          one line, per unit
**Status:** security findings, formatting and validation
**Needed:** approval of a destroy, a fix, or review and merge
<details> per unit: terraform's own diff </details>
```

- One row per change that matters, in fixed wording: ``tag `owner` ``,
  ``setting `public_network_access_enabled` ``, ``setting `topics`: added `x` ``,
  ``diagnostic settings added: logs `AuditEvent` → workspace `law-…` ``. The
  resource type becomes a readable name from the dictionary in
  `plan_details.py` (`KINDS`; any other type is spelled out from its name).
- Left out: values known only after apply, computed IDs, etags, URLs and
  timestamps (`NOISE`), data sources, outputs, and imports that change
  nothing. The raw plan stays in the artifact `plan-<run id>` (`plan.txt`).
- Sensitive values never appear; the JSON plan never leaves the job that
  made it. A plan split into shares passes on only the rows
  (`plan-summary.py --export`), and `plan` merges them (`--import`).
- Over GitHub's size limit the details are shortened, the largest unit
  first, then left out; the tables come last, and the comment ends with what
  was cut.

**Apply comment**, on the pull request merged as the applied commit, a new
one per run (marker `terragrunt-apply-<run id>`), from the `report` job:

```
## ✅ Apply complete                     ## ❌ Apply failed
repo · merge commit · units · ⏱️ duration · run
icon | Resource | Name | What happened | Result     ✅ done, ❌ failed: <error>, ⏭️ not applied / not run
### ❌ Errors
### Verification
```

The rows are those of the plan of the same run (`changes.json` in the plan
artifact: the text of the tables only), in the past tense; the result of
each comes from the apply output (`apply.txt` and `destroy.txt` in the
artifact `apply-<run id>`).

**Verification** is `verification.md` of the apply job's verification
(Terragrunt: validate and verify), from the artifact `verification-<run id>`.
Another post-apply check adds its own: markdown (a `| Check | Result |
Detail |` table, under a `#### <name>` line) in any `*.md` file of an
artifact `verification-<run id>-<name>`, uploaded by a job that the `report`
job `needs`. Every `verification-<run id>*` artifact goes in, in name order;
a leading `### Verification` line is dropped. Without any, the section says
that no check has reported.

## Conventions

The conventions job reads `.github/conventions.yaml` of the calling repository
(`conventions.yaml` in the root where it has not moved yet; another path with
the `conventions-config` input): which rules apply, how strictly, and the
exceptions. The rules are in `src/conventions.py`.

```yaml
units:                       # the unit files, for the terragrunt rules
  - mg-corp/**/terragrunt.hcl
rules:
  tags-required:
    level: error             # error fails the check, warn only reports, off skips
    allowed_values: _common/tags.hcl
    files:
      rg.hcl: [criticality, data_classification]
exceptions:
  - rule: no-hardcoded-location
    path: mg-corp/policy-assignments/terragrunt.hcl
    match: westeurope        # optional: text on the line or in the message
    reason: The allowed-locations policy lists the regions it allows.
    review: 2027-05-19       # when to look again (below)
```

| Rule | What it holds | Parameters |
|---|---|---|
| `variable-no-default` | a module variable has no `default`, and no `optional(type, default)` | `files` |
| `variable-documented` | a module variable has a `description` and a `type` | `files` |
| `module-source` | a unit takes its module from git with `modules_ref` | `pattern` |
| `dependencies-in-common` | no `dependency` block in a unit, only in the shared directory | `dir` |
| `mock-outputs` | every dependency has mocks, and the mocks never reach apply or destroy | `dir`, `mocks_file`, `forbidden_commands` |
| `tags-required` | context files set their tags, with an allowed value | `files`, `allowed_values` |
| `no-hardcoded-ids` | no subscription or tenant ID and no `/subscriptions/<id>` written out in a unit | `id_files` |
| `no-hardcoded-location` | no region written out in a unit | `locations` |
| `no-envcommon` | no `_envcommon` | - |
| `lock-none-needs-reason` | `lock = "none"` in a resource group of the listed criticality has a comment on or above the line | `criticality` |

Each violation is an annotation on its line and a row in the job summary. An
exception without a `reason`, an unknown rule or level fail the check.

## Exceptions and their review

Every exception file of ours has the same entry: what (`rule`, or `id` for
trivy), where (`path`), why (`reason`; `statement` for trivy, the
`description` for gitleaks) and `review`, the date (YYYY-MM-DD) by which it is
looked at again. `src/exception_review.py` reads them all - in the
conventions job (`conventions.yaml`, `sentinel-lint.yaml`, `trivyignore.yaml`),
in `content-sentinel.yml` and, for `.gitleaks.toml`, in `security.yml`. A
missing reason or a malformed date fails; a review date that has passed, or
one that is missing, is a warning - the finding stays accepted until someone
decides. The table with every check and its config:
[`standards/pipelines.md`](https://github.com/bearclave/governance/blob/main/standards/pipelines.md#checks-and-their-exceptions)
of governance.

```toml
[[allowlists]]
description = """
Why this is not a secret.
review: 2027-05-19
"""
regexes = ['''...''']
```

## Site checks

`static-web-app.yml` runs `src/site_checks.py` over the app location of every
site: `staticwebapp.config.json` (valid, no route twice, rewrite targets
exist), its `globalHeaders` (a CSP without `'unsafe-inline'`/`'unsafe-eval'`
for scripts, framing forbidden, HSTS, `nosniff`, `Referrer-Policy`), the pages
against that CSP (inline scripts, handlers, styles, `data:`), well-formed HTML
(doctype, `lang`, charset, title, end tags, unique ids) and the internal links
and anchors. What only one site checks (its sitemap, hreflang, the tests of
its API) stays in its `site-checks` input.

## ESLint

`checks.yml` and `static-web-app.yml` run ESLint over the JavaScript git
tracks, with the repository's `eslint.config.mjs` - a managed file of
governance `tools/sync.py` (the recommended rules; the repository's own
globals, ignores and rule levels below its marker). A repository whose
`package.json` pins `eslint` in `devDependencies` runs that version from its
lock file; any other runs the versions pinned in
`templates/_fragments/eslint-steps.yml` through `npx`. Errors fail, warnings
are annotations.

The script reads HCL with a small parser of its own (blocks, attributes,
strings, heredocs, comments) and needs only the Python standard library; the
workflow turns the YAML into JSON with the `yq` of the runner.

## Sentinel analytic rules

The Sentinel job reads `.github/sentinel-lint.yaml` of the calling repository
(`sentinel-lint.yaml` in the root where it has not moved yet, or the
`sentinel-config` input):

```yaml
rules:
  - path: mg-corp/.../analytic-rules/rules
    format: pair                  # <name>.json + <name>.kql, ${placeholders}
    substitutions_from: mg-corp/.../analytic-rules/terragrunt.hcl
  - path: ARM/4-AnalyticRules
    format: arm                   # ARM templates with .../alertRules inside
watchlists:                       # where `alias = "X"` or "watchlistAlias" is defined
  - mg-corp/.../watchlists/terragrunt.hcl
checks:                           # error unless listed
  watchlist-exists: warn
exceptions:
  - rule: tactics                 # the check
    path: ARM/**/an-mdca-*.json   # a glob of the file
    reason: why it is accepted
    review: 2027-05-19
```

The first format (`check:` for the check, `rule:` for the glob) is still read.

| Check | What it catches |
|---|---|
| `json-valid` | a rule file that is not JSON |
| `kql-present` | a scheduled or NRT rule without a query (the `.kql` beside a pair missing) |
| `kind`, `severity`, `trigger` | values outside what the alertRules API takes |
| `schedule` | frequency or period outside 5 minutes to 14 days, not ISO 8601, or a frequency longer than the period - the events in between are never seen |
| `tactics` | a tactic outside the API's AttackTactic |
| `techniques` | a technique that is not `Tdddd`, a sub-technique without its technique |
| `alert-details` | a broken `{{column}}` in the alert name or description, more than 3 columns |
| `entities` | more than 10 mappings, an unknown entity type, other than 1 to 3 identifiers |
| `custom-details` | more than 20 custom details |
| `grouping` | grouping by an entity or custom detail the rule does not have, `Selected` with nothing selected |
| `unique-name` | two rules with the same display name |
| `substitutions` | a `${placeholder}` the unit's `substitutions` do not fill (pair format) |
| `watchlist-exists` | `_GetWatchlist('X')` for a watchlist the repository does not define |

The KQL itself is not run: that needs a workspace and an identity that may
read it.

## Terragrunt: validate and verify

`terragrunt.yml` runs these jobs (checks `pipeline / <job>`; only
`pipeline / gate` is required, and it needs all of them):

```text
changes -> select -> validate ----------------------------\
                  \-> plan (1 of n) .. plan (n of n) -> plan -> apply -> verify
                                                                        \-> gate (always)
```

**changes** decides on a pull request whether anything can change a plan
(`src/terragrunt/changes.py`): a path has to match the caller's
`relevant-paths` and not be one that never reaches a plan -
`.github/dependabot.yml`, `.github/release.yml`, `.claude/**` and Markdown
outside every unit. Otherwise the rest is skipped and the gate passes.

**select** takes seconds and signs in nowhere: the base commit, the unit
selection (the one plan and apply use) and its split into the plan jobs.
**validate** and the plan jobs both start from it, side by side, so the
minutes of `terraform validate` no longer come before the plan. The `plan`
job, its comment (`formatting and validation`), apply and the gate all wait
for validate: a red validate keeps a pull request from merging and `main`
from applying. Both fail with an annotation that says what to do:

| Job | Step | Fails when |
|---|---|---|
| select | Principal references | a principal the change creates is referenced by display name (`principal-refs`) |
| select | Select the units | a unit directory moved (its state key would change) |
| validate | HCL formatting | `terragrunt hcl fmt --check` finds an unformatted file |
| validate | HCL validation | `terragrunt hcl validate` cannot read a file |
| validate | Terraform validate | `terraform validate` (its init with `-backend=false`) fails in a selected unit; dependencies answer with their mock outputs for validate (`--no-dependency-outputs`) |
| validate | Lock files | a committed `.terraform.lock.hcl` had to change in init; skipped when none is committed |

**plan** is bound by the runner's CPU (two cores; every unit starts terraform
and its providers several times), so select splits a large selection into
shares of up to 20 units, at most `plan-shards` (default 3) of them. Each
`plan (k of n)` job plans its share in dependency order and passes on its
`plan.txt` and what the comment shows of its JSON plans (`plan-summary.py
--export`); the JSON plans themselves never leave the job. `plan` reads them
back (`--import`) into one summary, comment and verdict. A unit whose
dependency is in another share reads that dependency's outputs from its
state, as any selection does. The whole tree of `iac-azure` (95 units) plans
in under 4 minutes instead of about 8 (each plan job about 2 minutes of terraform instead of 6).

**Verification** runs in the apply job right after the apply, so it adds a
minute or two rather than a job of its own:

1. *Plan again* - the applied units once more, at once, with
   `-detailed-exitcode`: anything but "no changes" fails.
2. *Failed Azure operations* - `az monitor activity-log list --status Failed`
   in the subscriptions of the applied units' azurerm providers (and
   `subscription-id`), from a minute before the apply until now, filtered
   to the apply identity. Skipped where no unit has an azurerm provider
   (`iac-github`). The Activity Log lags by a minute or more, so an
   operation that failed at the very end may be missed; a log that cannot
   be read is reported as `unreadable`, not as a failure.
3. *Check blocks* - every `check {}` that failed, from the second plan's JSON
   and from the warnings in the apply log.

The result is `verification.md` (a `| Check | Result | Detail |` table, the
body of the apply comment's `### Verification` section) and
`verification.json`, in the job summary and in the artifact
`verification-<run id>` that the apply comment picks up. `verification.json`,
version 1:

```json
{
  "version": 1,
  "result": "passed | failed",
  "commit": "<sha>",
  "run_url": "https://github.com/<repo>/actions/runs/<id>",
  "replan": {"result": "no-changes | changes | error | skipped", "exit_code": 0,
             "units_with_changes": [], "errors": []},
  "activity_log": {"result": "passed | failed | skipped | unreadable", "reason": "...",
                   "subscriptions": [], "window": {"start": "...", "end": "..."},
                   "failures": [{"time": "...", "operation": "...", "resource": "...",
                                 "status": "...", "sub_status": "...", "message": "..."}]},
  "checks": {"result": "passed | failed | none", "passed": 0, "unknown": 0,
             "failed": [{"unit": "...", "check": "...", "message": "...", "source": "plan | apply"}]}
}
```

**verify** is the only job that writes to the repository
(`contents`, `pull-requests`, `issues: write`). When the verification
failed it turns red and, without rolling anything back:

- opens a `Bug` issue `Apply of <sha> failed its verification` (`prio:high`,
  `source:drift`, assigned to the owner) with the table, the JSON and the
  run link, or comments on the open one;
- on a push, pushes `revert/<short-sha>` with `git revert` of the commit
  (`-m1` for a merge commit) and opens it as a draft pull request
  `revert: <original title>`. A pull request opened with the workflow token
  starts no checks until someone pushes or reopens it. Where the repository
  does not let GitHub Actions create pull requests, or the commit changes a
  workflow file, the issue says so and links the branch or asks for a revert
  by hand. A manual run has no single commit and gets only the issue.

The caller grants those permissions to the `pipeline` job (`contents: write`,
`issues: write` next to the rest); without them the workflow does not start.

`terragrunt-drift.yml` reports failed check blocks of the whole tree as
well, and fails on them.

## How a script gets into a workflow

A reusable workflow runs in the calling repository, and the caller's token
cannot read this private repository: a script here cannot be checked out next
to the workflow. So it travels inside it. `templates/<name>.yml` has a line
`@@include src/<script>@@` inside a heredoc, and `tools/build.py` writes
`.github/workflows/<name>.yml` with the script in its place.
`@@include templates/_fragments/<name>.yml@@` puts a shared piece of workflow
(the steps that unpack the scripts into `$RUNNER_TEMP/ci` and put them on
`PATH`, the conventions and Sentinel jobs, the gate) in the same way.

Edit the template, the fragment or `src/`, never the generated file, and run
`tools/build.py`; the `pipeline` job fails when the two do not match.

| Directory | Content | Tests |
|---|---|---|
| `src/` | `conventions.py`, `sentinel_lint.py`, `exception_review.py`, `site_checks.py`, `pr_check.py`, `layout_check.py`, `install-tools.sh`, `pr-comment.sh`; `major_tag.py` (run by `major-tag.yml` of this repository); `layout.yaml` (managed copy from governance) | `tests/test_*.py`, `pr_check.py --self-test`, `layout_check.py --self-test` |
| `src/terragrunt/` | unit selection, plan and apply comments, principal references, stale locks, analytic rules outside code, trivy over plans | `tests/terragrunt/` |
| `tools/` | `build.py`, `export-for-templates.py`, `sync-templates.py`; `sanitise-plan.py` turns plans of a run into test fixtures | |
| `src/m365/` | module install, type selection, plan outputs | `tests/m365/` (Pester) |

A script reads the repository it runs for from the working directory (the
root of the caller's checkout), never from its own location.

## Versions

[Semantic versioning](https://github.com/bearclave/governance/blob/main/standards/versioning.md):
every release is a signed tag `v2.Y.Z` and a GitHub release with notes
generated from the pull request labels; the major tag `v2` moves to the newest
one.

- Callers use `@v2`, so a fix reaches every repository when the tag moves.
- Repositories that touch production may pin the exact `@v2.Y.Z`; Dependabot
  then proposes the bump as a pull request with its plan.
- `v1` stays where it is for anything that still calls it; it gets no changes.
- The `release-tags` ruleset refuses to update or delete a `v*.*.*` tag; only
  an organisation owner and the GitHub App `bearclave-bot` may move `v2`
  (`major-tag.yml` on every published release; `tools/move-major-tag.sh` by
  hand).
- Everything the workflows themselves use is pinned to a commit; the
  organisation enforces that (`sha_pinning_required`).

A change that would break callers (a new required input, a renamed job and
with it the check name) is a major version.

The tools (gitleaks, actionlint, zizmor, shellcheck) are release binaries
checked against a sha256, pinned in the `env` of `security.yml`; the tools of
the pipelines (terraform, terragrunt, tflint, trivy, Microsoft365DSC) in the
caller's `.github/tool-versions.env`. Dependabot does not see them; a version
and its checksum are bumped together by hand. The checksum is the `digest`
GitHub publishes with the release asset:

```bash
gh api repos/gitleaks/gitleaks/releases/tags/v8.30.1 \
  --jq '.assets[] | select(.name | test("linux_x64")) | .digest'
```

## Releasing

1. Change the workflow in a pull request. The pull request runs `ci.yml` and
   `pr-check.yml` from its own branch, so the changed workflows are tried on
   this repository first, along with the unit tests.
2. Merge.
3. Tag and publish the release:

   ```bash
   git switch main && git pull --ff-only
   git tag -s v2.1.0 -m v2.1.0 && git push origin v2.1.0
   gh release create v2.1.0 --verify-tag --generate-notes
   ```

   Publishing the release runs `major-tag.yml`, which moves `v2` to it as
   the GitHub App `bearclave-bot` (`src/major_tag.py`): only to a published,
   non-pre-release `vX.Y.Z` with a verified signed tag that is the newest of
   its major, and never backwards. `v2` becomes a lightweight ref to the
   release's commit; the release tags stay the owner's signed tags. Check
   the run with `gh run list --workflow major-tag.yml --limit 1`; rerun it
   for a release with `gh workflow run major-tag.yml -f release=v2.1.0`
   (a no-op when `v2` is already there).

   Manual fallback, and the first tag of a new major (`v3`), which the
   workflow does not create: `tools/move-major-tag.sh v2.1.0`. It is the
   one force push an agent may run (allowed for this script alone).

   `release.yml` works out the same version and notes in a dry run; this
   repository releases by hand, because its tags are signed.

4. Rerun the pipeline in one caller to see it there too.

## Repository settings

Branch protection, required checks, merge settings and labels are defined in
[`bearclave/iac-github`](https://github.com/bearclave/iac-github)
(`repositories/ci`). Engineering standards:
[`bearclave/governance`](https://github.com/bearclave/governance/tree/main/standards).
