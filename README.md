# ci

Shared CI for the `bearclave` organisation. The checks that are the same in
every repository live here once, as reusable workflows, instead of a copy in
each repository that drifts on its own.

## Workflows

| Workflow | What it checks | Check name in the caller |
|---|---|---|
| `security.yml` | gitleaks over the whole history, actionlint, zizmor, shellcheck | `security / scan` |
| `conventions.yml` | the code conventions the repository lists in `conventions.yaml` | `conventions / check` |
| `sentinel-lint.yml` | Microsoft Sentinel analytic rules, as `sentinel-lint.yaml` says where they are | `sentinel / lint` |

The `main-checks` ruleset of the organisation requires `security / scan` on
every repository that has workflows.

## Calling a workflow

```yaml
jobs:
  security:
    uses: bearclave/ci/.github/workflows/security.yml@v1
    permissions:
      contents: read
```

The repository is private; its Actions access is set to "Accessible from
repositories in the 'bearclave' organization", which is what lets the others
call it.

**Template repositories cannot.** `template-iac-*` are copied to customers,
whose organisations cannot reach a private `bearclave` repository, so the
templates call a local copy (`uses: ./.github/workflows/security.yml`). The
copies are written by `export-for-templates.py`, which takes out what belongs
to this organisation (the header, zizmor's allowance for `bearclave/*`):

```bash
./export-for-templates.py ../template-iac-modules ../template-iac-terragrunt ../template-iac-m365
./export-for-templates.py --check ../template-iac-*     # are the copies current?
```

Run it after every release, in a pull request per template.

`sync-templates.py` does the same for everything else a template shares with
its live repository - the pipeline, the scripts and their tests, the runner of
`iac-m365`, the modules - and keeps what is the tenant (account.hcl, the units,
the configuration, the README). Each template has its rules in the script:
what is copied, what is replaced (`bearclave/iac-modules` -> `contoso/...`),
and the edits a template needs on top (the jobs that sign in to a tenant wait
for the customer's variables). It stops when a file would bring the
organisation or its tenant into a template, and when an edit no longer finds
its text because the live file changed shape:

```bash
./sync-templates.py --check ..                       # what is out of date
./sync-templates.py .. template-iac-terragrunt       # write one template
```

## Conventions

`conventions.yml` reads `conventions.yaml` of the calling repository (another
path with the `config` input): which rules apply, how strictly, and the
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

The script reads HCL with a small parser of its own (blocks, attributes,
strings, heredocs, comments) and needs only the Python standard library; the
workflow turns the YAML into JSON with the `yq` of the runner.

## Sentinel analytic rules

`sentinel-lint.yml` reads `sentinel-lint.yaml` of the calling repository:

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
  - check: tactics
    rule: ARM/**/an-mdca-*.json   # a glob of the file
    reason: why it is accepted
```

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
`@@include src/<script>@@` inside a heredoc, and `./build.py` writes
`.github/workflows/<name>.yml` with the script in its place. Edit the template
or `src/`, never the generated file, and run `./build.py`; the `python` job
fails when the two do not match.

## Versions

Callers use the moving tag `v1`, not a commit:

- A change here reaches every repository at once, when the tag moves. With a
  commit pin, every change would mean a pull request in each of a dozen
  repositories, and in the IaC repositories each of those plans the whole
  tree.
- The `tagy-verzi` ruleset lets only an organisation owner move or delete a
  `v*` tag.
- Everything the workflows themselves use is pinned to a commit; the
  organisation enforces that (`sha_pinning_required`).

A change that would break callers (a new required input, a renamed job and
with it the check name) goes to `v2`, and the callers move over one by one.

The tools (gitleaks, actionlint, zizmor, shellcheck) are release binaries
checked against a sha256, pinned in the `env` of the workflow. Dependabot does
not see them; a version and its checksum are bumped together by hand. The
checksum is the `digest` GitHub publishes with the release asset:

```bash
gh api repos/gitleaks/gitleaks/releases/tags/v8.30.1 \
  --jq '.assets[] | select(.name | test("linux_x64")) | .digest'
```

## Releasing

1. Change the workflow in a pull request. The pull request runs the changed
   workflow from its own branch (`.github/workflows/ci.yml`), so it is tried
   on this repository before anything else sees it, along with the unit tests
   in `tests/`.
2. Merge.
3. Move `v1` to the merge commit:

   ```bash
   sha=$(gh api repos/bearclave/ci/commits/main --jq .sha)
   gh api -X PATCH repos/bearclave/ci/git/refs/tags/v1 -f sha="$sha" -F force=true
   ```

4. Rerun the check in one caller to see it there too.
