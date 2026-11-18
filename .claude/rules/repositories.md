# Repositories

<!-- Standard of bearclave/governance (standards/repositories.md). In other repositories this is a managed copy - change it in governance. -->

## Groups and names

GitHub has no folders. A repository's group shows in four places that always
agree: the **name prefix**, the custom property **`group`**, a **topic** and
the organisation **Project** of the group. Locally the group is a folder in
`~/Repositories/Bearclave.GitHub/`.

| Group | Prefix | Project | Local folder | What belongs here |
|---|---|---|---|---|
| `iac` | `iac-` | IaC | `iac/` | configuration applied by a pipeline, and the modules it uses |
| `app` | `app-` | Apps | `app/` | applications and websites, and tools that build something |
| `template` | `template-`, `content-` | Templates & content | `template/` | what is copied to a customer: IaC templates, content catalogues |
| `platform` | none | Platform | `platform/` | how the organisation works: `ci`, `governance`, `handbook`, `ops-tools`, `.github` |

Name: `<prefix><subject>`, lowercase, words joined by `-`, English, the
subject says what the repository manages (`iac-azure`, `app-web-bearclave`,
`content-sentinel`). No `-repo`, no `-new`, no year.

## Custom properties

Set on every repository in `bearclave/iac-github`; the rulesets target them.

| Property | Values | Drives |
|---|---|---|
| `group` | `iac`, `app`, `template`, `platform` | grouping, Project, topic |
| `pipeline` | `terragrunt`, `terragrunt-home`, `terraform-modules`, `m365-dsc`, `static-web-app`, `container`, `content-sentinel`, `checks`, `ci`, `template` | which shared workflow `ci.yml` calls, which checks are required |
| `entity` | `sro`, `osvc`, `shared` | whose the repository is (Bearclave s.r.o., the sole trader, both) |
| `touches-production` | `true`, `false` | branch up to date before merge, `/security-review` |
| `direct-commit` | `true`, `false` | `true` only for repositories written straight to `main` (no pull request) |

## What every repository contains

| Path | Owner | Content |
|---|---|---|
| `README.md` | repository | documentation, structure in [documentation.md](https://github.com/bearclave/governance/blob/main/standards/documentation.md) |
| `CLAUDE.md` | repository | working rules specific to the repository ([templates/CLAUDE.md](https://github.com/bearclave/governance/blob/main/standards/templates/CLAUDE.md)) |
| `.claude/settings.json` | managed | denied commands, no attribution |
| `.claude/rules/*.md` | managed | these standards |
| `.github/workflows/ci.yml` | repository | calls the shared workflow of its `pipeline` ([templates/workflows/](https://github.com/bearclave/governance/tree/main/standards/templates/workflows/)) |
| `.github/workflows/pr.yml` | managed | the `pr` check: title, branch, labels, assignee |
| `.github/workflows/drift.yml` | repository | only where something is applied: the weekly drift |
| `.github/dependabot.yml` | repository | from [templates/dependabot.yml](https://github.com/bearclave/governance/blob/main/standards/templates/dependabot.yml), plus the repository's ecosystems |
| `.github/release.yml` | managed | release note categories |
| `.github/tool-versions.env` | repository | pinned tools with sha256, where the pipeline installs any |
| `.editorconfig` | managed | indentation and line endings |
| `.gitignore` | repository | |

"Managed" files are written by `governance/sync.py` and carry a header
saying so; a local edit is overwritten by the next sync. Pull request and
issue templates are not copied: GitHub takes them from `bearclave/.github`.
The `template-*` repositories get none of the managed files: they are copied
to customers and must not name the organisation.

## Where the settings live

Nothing about a repository is set by hand in the GitHub UI. Name,
description, topics, visibility, merge settings, custom properties,
environments, variables, labels, team access and rulesets are code in
[`bearclave/iac-github`](https://github.com/bearclave/iac-github); a change
there is a pull request like any other.

## A new repository

1. Add it to `iac-github` (`repositories/<name>/terragrunt.hcl`) with its
   group, pipeline and properties; the merge creates it with labels,
   rulesets and settings.
2. Clone it into the folder of its group, `git novy feat/skeleton`.
3. Run `governance/sync.py` for it, add `README.md`, `CLAUDE.md`,
   `.github/workflows/ci.yml` and `dependabot.yml` from the templates.
4. Open the first pull request; it must already pass `security / scan`,
   `pr / check` and `pipeline / gate`.
5. Add it to the table in `work/repos.md` of the LLM wiki.

## Archiving

A repository nobody works on any more is archived (`archived = true` in
`iac-github`), not deleted. It stays in its Project and keeps its history.
