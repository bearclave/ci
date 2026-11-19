# Repository layout

<!-- Standard of bearclave/governance (standards/layout.md). In other repositories this is a managed copy - change it in governance. -->

Every repository is laid out the same way, so a person or an agent finds
things without reading the README first. The machine-readable form is
[layout.yaml](https://github.com/bearclave/governance/blob/main/standards/layout.yaml); the layout check in `pr / check` refuses a pull
request whose root breaks it.

## The root is minimal

Only what something reads from the root by itself:

| What | Files |
|---|---|
| Git and GitHub | `README.md`, `.gitignore`, `.gitattributes`, `.editorconfig`, `LICENSE` (only where the code is handed over) |
| Ecosystem manifests | `package.json` + lock, `pyproject.toml` + `uv.lock`, `Dockerfile`, `.dockerignore` |
| Terragrunt context | `root.hcl`, `account.hcl` (terragrunt pipelines); `tenant.psd1` (`iac-m365`) |
| Tools that look in the root | `.gitleaks.toml`, `.tflint.hcl`, `eslint.config.mjs`, `.mcp.json` (Claude Code) |
| Folders | the vocabulary below and the content folders of the pipeline |

Everything else goes into a folder. A config that only the pipeline reads
belongs in `.github/`, a script in `tools/`, a document in `docs/`.

## `.claude/` and `.github/`

| Path | Holds |
|---|---|
| `.claude/CLAUDE.md` | working rules specific to the repository ([templates/CLAUDE.md](https://github.com/bearclave/governance/blob/main/standards/templates/CLAUDE.md), at most 30 lines) |
| `.claude/rules/`, `.claude/settings.json` | managed copies (`tools/sync.py`): the always-rules and the language rules of the languages the repository has |
| `.github/workflows/` | callers of `bearclave/ci` |
| `.github/CODEOWNERS`, `.github/SECURITY.md`, `.github/ISSUE_TEMPLATE/`, `.github/pull_request_template.md` | managed copies (`tools/sync.py`) |
| `.github/markdownlint.yaml` | markdownlint rules that differ from `ci` |
| `.github/dependabot.yml`, `.github/release.yml`, `.github/tool-versions.env` | GitHub and CI |
| `.github/conventions.yaml` | conventions check, and the exceptions to this layout |
| `.github/sentinel-lint.yaml` | Sentinel lint |
| `.github/trivyignore.yaml` | accepted trivy findings, each with a reason and a review date |
| `.github/pssa.psd1` | PSScriptAnalyzer settings |

No scripts in `.github/`. The exception are the `template-*` repositories,
whose exported pipeline copies carry their scripts in `.github/scripts/`.

## Folders

English, plural, the same names everywhere:

| Folder | Holds |
|---|---|
| `src/` | code |
| `tests/` | tests, mirroring `src/` |
| `tools/` | scripts a person runs: build, generators, bootstrap, operations |
| `docs/` | longer documents (below) |
| `_common/` | shared code that is neither a unit nor a module (Terragrunt includes, `_common/` of modules) |

Only in the group `app` and in `iac-m365`, each with a line in the README
Layout section:

| Folder | Holds |
|---|---|
| `config/` | configuration the code reads |
| `content/` | content the product publishes or renders |
| `data/` | input data |
| `deploy/` | what runs the product where it is deployed |
| `lib/` | libraries the code shares |

Content folders of a pipeline:

| Pipeline | Content folders |
|---|---|
| `terragrunt`, `terragrunt-home` | folders holding units (`mg-corp/`, `repositories/`, ...) |
| `terraform-modules` | folders holding modules (`identity/`, `sentinel/`, ...) |
| `static-web-app` | `api/` (the Functions folder of the site) |
| `content-sentinel` | `ARM/`, `terragrunt/` |
| `ci` | `templates/` |

A Terragrunt unit directory never moves: its path is its state key.

Names of product content that is Czech by nature (the templates of
`app-office-templates`) may stay Czech; the folders around them follow the
vocabulary.

## docs/

- Flat, kebab-case names: `docs/break-glass.md`.
- Fixed names where they apply: `setup.md`, `manual-steps.md`,
  `out-of-scope.md`, `decisions.md`, `catalog.md`.
- A subfolder only once `docs/` has eight files or more.
- The README links every file in `docs/`.

## Exceptions

An entry the layout does not allow is either moved or listed with a reason
in `.github/conventions.yaml` (the conventions check ignores this section):

```yaml
layout:
  exceptions:
    - path: standards
      reason: The standards are the content of this repository.
```

`path` is a root entry, or `.github/<name>`, `docs/<name>`; globs are
allowed. An exception without a reason fails the check.

## .gitattributes

Managed by `tools/sync.py` in every repository: `* text=auto eol=lf`, the
binary formats, and below the marker a section the repository owns - the
paths it generates, marked `linguist-generated=true` so that they collapse
in diffs and do not count for the size label.
