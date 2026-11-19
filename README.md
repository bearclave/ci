# ci

Shared CI for the `bearclave` organisation. The checks that are the same in
every repository live here once, as reusable workflows, instead of a copy in
each repository that drifts on its own.

## Layout

```text
templates/            the workflows, with @@include markers; _fragments/ shared pieces
.github/workflows/    generated from templates/ by tools/build.py (do not edit)
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
| `pr.yml` | `pr` | every repository | Conventional Commit title, `<type>/<slug>` branch, description sections; sets `type:*`, `breaking`, `size:*` labels and the assignee (`src/pr_check.py`); the root layout (`src/layout_check.py`, below) |
| `checks.yml` | `pipeline` | `governance`, `handbook`, `ops-tools`, `app-office-templates`, `vs-code-extensions`, `.github` | ESLint where there is JavaScript, an optional command of the repository, then the gate |
| `terragrunt.yml` | `pipeline` | `iac-azure`, `iac-github` | conventions, Sentinel lint, plan of the affected units with trivy and a PR comment, apply from `main`, destroys held back, gate |
| `terragrunt-drift.yml` | `drift` | `iac-azure`, `iac-github` | weekly plan of the whole tree, trivy, analytic rules outside the code |
| `terragrunt-home.yml` | `pipeline` | `iac-home` | conventions, plan and apply of the router through WireGuard, certificate renewal and drift on schedule |
| `terraform-modules.yml` | `pipeline` | `iac-modules` | fmt, validate per module, tflint, trivy config, conventions |
| `m365-dsc.yml` | `pipeline` | `iac-m365` | schema check, Pester, PSScriptAnalyzer, plan of the affected types, apply |
| `m365-drift.yml` | `drift` | `iac-m365` | weekly plan of the whole tenant, tombstones into a pull request |
| `static-web-app.yml` | `pipeline` | `app-web-*` | the common site checks, ESLint, the site's own checks (optional), deploy to the Static Web App when the content changed |
| `container.yml` | `pipeline` | `app-home` | lint and tests in the image, push to GHCR from `main` |
| `content-sentinel.yml` | `pipeline` | `content-sentinel` | Sentinel lint, generated views up to date |
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
| `src/` | `conventions.py`, `sentinel_lint.py`, `exception_review.py`, `site_checks.py`, `pr_check.py`, `layout_check.py`, `install-tools.sh`, `pr-comment.sh`; `layout.yaml` (managed copy from governance) | `tests/test_*.py`, `pr_check.py --self-test`, `layout_check.py --self-test` |
| `src/terragrunt/` | unit selection, plan summary and details, principal references, stale locks, analytic rules outside code, trivy over plans | `tests/terragrunt/` |
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
  an organisation owner may move `v2`.
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
3. Tag the release and move the major tag:

   ```bash
   git switch main && git pull --ff-only
   git tag -s v2.1.0 -m v2.1.0 && git push origin v2.1.0
   gh release create v2.1.0 --verify-tag --generate-notes
   git tag -fs v2 -m "v2 -> v2.1.0" "v2.1.0^{}" && git push -f origin v2
   ```

4. Rerun the pipeline in one caller to see it there too.

## Repository settings

Branch protection, required checks, merge settings and labels are defined in
[`bearclave/iac-github`](https://github.com/bearclave/iac-github)
(`repositories/ci`). Engineering standards:
[`bearclave/governance`](https://github.com/bearclave/governance/tree/main/standards).
