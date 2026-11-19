# Checks

What the shared checks hold, where their config is in the calling repository
and how an exception is written. The table of every check and its config is
also in [`standards/pipelines.md`](https://github.com/bearclave/governance/blob/main/standards/pipelines.md#checks-and-their-exceptions)
of governance.

## pr / check

`pr.yml`, required in every repository, in one job:

| Step | Holds | Config |
|---|---|---|
| Title, branch, description, labels | Conventional Commit title (a bot's title over 72 characters only warns), `<type>/<slug>` branch, the six description sections for people and Apps (Dependabot excepted); sets `type:*`, `breaking`, `size:*`; assigns the author, or `vars.OWNER` when the author is an App (`src/pr_check.py`) | - |
| Root layout | the root against `standards/layout.yaml` of governance (`src/layout.yaml`, a managed copy): required and allowed root files, the folders of the repository's group and pipeline (and those it forbids: `tools/` and `deploy/` in a Terragrunt repository), what `.github/` holds, the names in `docs/` (`src/layout_check.py`) | `layout: exceptions:` in `.github/conventions.yaml` |
| To-dos, links and collections | `todo-issue`, `markdown-links` and `collection-schema` of `src/conventions.py` (below) | `.github/conventions.yaml` |
| Markdown | markdownlint (`markdownlint-cli2`, image pinned by digest) over the Markdown git tracks, with the rules of `src/markdownlint.yaml` | `.github/markdownlint.yaml`: its keys replace the defaults rule by rule |

`.claude/` is left out of the last two: the standards there are managed
copies, checked in governance where they are written.

```yaml
# .github/markdownlint.yaml - markdownlint rule settings
MD013:
  line_length: 120
MD033: true
```

## Conventions

`src/conventions.py` reads `.github/conventions.yaml` of the calling
repository (`conventions.yaml` in the root where it has not moved yet;
another path with the `conventions-config` input): which rules apply, how
strictly, and the exceptions. The pipelines' `conventions` job runs the named
rules; `todo-issue` and `markdown-links` run in every repository without being
named (in `pr / check`), unless set to `off`.

```yaml
units:                       # the unit files, for the terragrunt rules
  - mg-corp/**/terragrunt.hcl
rules:
  tags-required:
    level: error             # error fails the check, warn only reports, off skips
    allowed_values: _common/tags.hcl
    files:
      rg.hcl: [criticality, data_classification]
  todo-issue:
    level: warn
exceptions:
  - rule: no-hardcoded-location
    path: mg-corp/policy-assignments/terragrunt.hcl
    match: westeurope        # optional: text on the line or in the message
    reason: The allowed-locations policy lists the regions it allows.
    review: 2027-05-19       # when to look again
```

| Rule | What it holds | Parameters |
|---|---|---|
| `variable-no-default` | a module variable has no `default`, and no `optional(type, default)` | `files` |
| `variable-documented` | a module variable has a `description` and a `type` | `files` |
| `module-source` | a unit takes its module from git with `modules_ref`, in its own `terraform` block (`pattern`, default the context of `include.root.locals`, `include.root.locals.ctx` or `local.ctx`) or in the `_common/units/<module>.hcl` it includes (`common_pattern`, default `${<any>.modules}//<path>?ref=${<any>.modules_ref}`) | `pattern`, `common_pattern` |
| `dependencies-in-common` | no `dependency` block in a unit, only in the shared directory or `_common/units/` | `dir` (or `dirs`) |
| `mock-outputs` | every dependency (in the shared directory and `_common/units/`) has mocks, and the mocks never reach apply or destroy | `dir`, `mocks_file`, `forbidden_commands` |
| `tags-required` | context files set their tags, with an allowed value | `files`, `allowed_values` |
| `no-hardcoded-ids` | no subscription or tenant ID and no `/subscriptions/<id>` written out in a unit | `id_files` |
| `no-hardcoded-location` | no region written out in a unit | `locations` |
| `no-envcommon` | no `_envcommon` | - |
| `unit-assets` | a file in the unit tree that is not HCL: only a path of `allowed` (globs relative to the unit, default `**/*.yaml`, `definition.json`, `workflows/*/workflow.json`), read by the unit: named in its `terragrunt.hcl`, or matched by a `fileset()` pattern of the unit or of the `_common/units/` file it includes | `allowed`, `paths` (default `infrastructure/**`, plus every unit directory) |
| `collection-schema` (every repository) | every object of a collection (below) is `<key>.yaml`, its key is lowercase words joined by `-` and unique in the collection, and it matches the schema of its module; the schema is usable and closed | `schemas` (default `.github/schemas`), `collections` (folders outside the units: `[{path, schema}]`), `paths` (default `infrastructure/**`) |
| `scope-layout` | under `root` (default `infrastructure`): `mg-*` holds `mg.hcl`, its unit, `global/`, `mg-*/`, `sub-*/`; `sub-*` holds `subscription.hcl`, its unit, `global/`, regions; a region `region.hcl` and `rg-*/`; `rg-*` `rg.hcl`, its unit and units; a folder with `device.hcl` that file and units; `global/` units only; a unit inside another unit fails | `root` |
| `folder-dictionary` | a unit folder is named after its module, `<module>` or `<module>-<purpose>`; a `_common/units/` file `<module>.hcl`. `mg-*`, `sub-*`, `rg-*` hold the units of their scope; a folder named in the plural (`repositories/`) holds one unit per instance, named after it | `folders` (`{<module>: [<glob>]}`, e.g. `repository: ["*"]`), `prefixes` (a folder may leave out the platform word: with `sentinel-`, `analytic-rules/` runs `sentinel-analytic-rules`) |
| `lock-none-needs-reason` | `lock = "none"` in a resource group of the listed criticality has a comment on or above the line | `criticality` |
| `todo-issue` (every repository) | a to-do comment names its issue: `TODO(#42): ...`; any tracked file but Markdown and binaries | `files`, `exclude` |
| `markdown-links` (every repository) | a relative link or image in Markdown leads to a file or folder of the repository, and its `#anchor` to a heading of the linked Markdown; offline, nothing is fetched; links in code are skipped | `exclude` |

Each violation is an annotation on its line and a row in the job summary. An
exception without a `reason`, an unknown rule or level fail the check. The
script reads HCL with a small parser of its own and needs only the Python
standard library; the workflow turns the YAML into JSON with the runner's
`yq`.

## Collections

A collection is a unit that reads its objects with `fileset()` - one
`<key>.yaml` per object, in organisational subfolders or not
(governance `code/terragrunt.md#collections`) - or a folder that
`collections` lists (the catalogue `content-sentinel`, which has no units).
`collection-schema` runs in `pr / check` of every repository, so a
repository without collections pays nothing and one without a pipeline
(the catalogue) is checked all the same.

- **Which schema:** `<schemas>/<module>.schema.json`, `<module>` the name of
  the module the unit runs (`azure/sentinel-analytic-rules` ->
  `.github/schemas/sentinel-analytic-rules.schema.json`); for a listed folder
  the `schema` of its entry. The schema is a copy of the module's
  `schema.json` in `iac-modules`, refreshed in the pull request that moves
  `modules_ref`.
- **The schema itself** must be closed - every object has
  `additionalProperties: false` or a schema for the values of a map -
  because Terraform drops an unknown key silently when it converts YAML to
  a typed object, so a typo would pass.
- **Reading the YAML:** the runner's `yq`, one document per file, the same
  YAML 1.2 that Terraform's `yamldecode` reads (`yes` stays a string).
- **The validator** is `src/schema_check.py`, a subset of JSON Schema
  2020-12 in the standard library: the reference implementation
  (`jsonschema`) needs `attrs`, `referencing` and the compiled `rpds-py`, so
  it cannot travel inside a workflow as one pinned file, and the pipelines
  install nothing from PyPI. A keyword outside the subset fails the check
  instead of being ignored; the list is at the top of the script.
  `python3 schema_check.py <schema.json> <object.json>` runs it locally.

```yaml
# .github/conventions.yaml of content-sentinel
rules:
  collection-schema:
    collections:
      - path: content/analytic-rules
        schema: sentinel-analytic-rules
```

## Exceptions and their review

Every exception file has the same entry: what (`rule`, or `id` for trivy),
where (`path`), why (`reason`; `statement` for trivy, the `description` for
gitleaks) and `review`, the date (YYYY-MM-DD) by which it is looked at again.
`src/exception_review.py` reads them all - in the conventions job
(`conventions.yaml`, `sentinel-lint.yaml`, `trivyignore.yaml`), in
`content-sentinel.yml` and, for `.gitleaks.toml`, in `security.yml`. A
missing reason or a malformed date fails; a review date that has passed, or
one that is missing, is a warning. A layout exception may carry a `review`
date too; it is optional there.

```toml
[[allowlists]]
description = """
Why this is not a secret.
review: 2027-05-19
"""
regexes = ['''...''']
```

## Paths the workflows read in the caller

The configs moved from the root into `.github/` (governance `layout.md`); the
workflows read the new path and fall back to the old one:

| Config | Path | Before the move |
|---|---|---|
| conventions | `.github/conventions.yaml` | `conventions.yaml` |
| Sentinel lint | `.github/sentinel-lint.yaml` | `sentinel-lint.yaml` |
| trivy exceptions | `.github/trivyignore.yaml` | `.trivyignore.yaml` |
| PSScriptAnalyzer | `.github/pssa.psd1` | `.pssa.psd1` |
| content generators | `tools/*.py` | `_tools/*.py` |

## Sentinel analytic rules

The Sentinel job reads `.github/sentinel-lint.yaml` of the calling repository
(or the `sentinel-config` input):

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

| Check | What it catches |
|---|---|
| `json-valid` | a rule file that is not JSON |
| `kql-present` | a scheduled or NRT rule without a query |
| `kind`, `severity`, `trigger` | values outside what the alertRules API takes |
| `schedule` | frequency or period outside 5 minutes to 14 days, not ISO 8601, or a frequency longer than the period |
| `tactics` | a tactic outside the API's AttackTactic |
| `techniques` | a technique that is not `Tdddd`, a sub-technique without its technique |
| `alert-details` | a broken `{{column}}` in the alert name or description, more than 3 columns |
| `entities` | more than 10 mappings, an unknown entity type, other than 1 to 3 identifiers |
| `custom-details` | more than 20 custom details |
| `grouping` | grouping by an entity or custom detail the rule does not have |
| `unique-name` | two rules with the same display name |
| `substitutions` | a `${placeholder}` the unit's `substitutions` do not fill (pair format) |
| `watchlist-exists` | `_GetWatchlist('X')` for a watchlist the repository does not define |

The KQL itself is not run: that needs a workspace and an identity that may
read it. Planned for `iac-sentinel`, not built yet: a step of the plan job
that runs `<query> | take 0` of each changed analytic rule and hunting
query against the workspace of `workspace.hcl` as the plan identity (Log
Analytics Reader; the Log Analytics query API through ARM), which catches a
missing table, column, watchlist or function in about two seconds a rule.
It waits for the YAML format of the rules in `sentinel_lint.py` and the
first workspace in `iac-sentinel`.

## Site checks

`static-web-app.yml` runs `src/site_checks.py` over the app location of every
site: `staticwebapp.config.json` (valid, no route twice, rewrite targets
exist), its `globalHeaders` (a CSP without `'unsafe-inline'`/`'unsafe-eval'`
for scripts, framing forbidden, HSTS, `nosniff`, `Referrer-Policy`), the pages
against that CSP, well-formed HTML, the internal links and anchors, and
what search engines read: root-relative links, a canonical on every page
that is not `noindex`, hreflang sets that name the page itself and
`x-default`, a `sitemap.xml` of existing pages and every canonical. What
only one site checks stays in its `site-checks` input.

After a deployment the job requests the caller's `url` input and fails
unless it answers 200 within about two minutes (the smoke test).

## ESLint

`checks.yml`, `static-web-app.yml` and `container.yml` run ESLint, in their main job, over the
JavaScript git tracks, with the repository's `eslint.config.mjs` (a managed
file of governance `tools/sync.py`). Without JavaScript the steps are skipped
and cost nothing. A repository whose `package.json` pins `eslint` runs that
version from its lock file; any other runs the versions pinned in
`templates/_fragments/eslint-steps.yml` through `npx`. Errors fail, warnings
are annotations.
