---
paths:
  - "**/*.hcl"
---

# Terragrunt configuration

<!-- Standard of bearclave/governance (standards/code/terragrunt.md). In other repositories this is a managed copy - change it in governance. -->

Applies to `iac-azure`, `iac-home`, `iac-github` and `template-iac-terragrunt`.
Unit skeleton: [../templates/terragrunt/](https://github.com/bearclave/governance/tree/main/standards/templates/terragrunt/).

## Layout

- **The directory hierarchy is the context.** `root.hcl` reads the context
  files on the way up (`account.hcl` → `mg.hcl` → `subscription.hcl` →
  `region.hcl` → `rg.hcl` in `iac-azure`) and derives naming, tags, location,
  provider and state key from them.
- A **unit** (`terragrunt.hcl`) states only what is specific to its resource:
  no name, no region, no tags, no IDs written out.
- Directories mirror the real hierarchy (management groups, subscriptions,
  regions, resource groups; organisation and repositories in `iac-github`).
  A directory carries only the distinguishing part of the name; its prefix
  (`mg-`, `sub-`, `rg-`) says the level.
- **No `_envcommon`.** Every input, dependency and value belongs to the unit,
  so a unit can be read and changed on its own.
- Shared `dependency` blocks only in `_common/deps/`, each with mock outputs
  that never reach `apply` or `destroy`.

## Units

- The module from git with the pinned ref:
  `source = "${include.root.locals.modules}//<path>?ref=${include.root.locals.modules_ref}"`.
  Never a local path, also not for a quick try.
- `include "root"` with `expose = true` first; then the shared dependency
  includes; then `dependencies`; then `inputs`.
- Every input of the module is set - the modules have no defaults.
- A value that is an exception gets a comment saying why
  (`lock = "none"` in a high-criticality group needs one; the conventions
  check enforces it). Comments otherwise:
  [documentation.md](https://github.com/bearclave/governance/blob/main/standards/documentation.md#comments-in-code).

## State

- One state per unit; the key is the unit's path. **Moving a unit directory
  is refused** by the pipeline: delete it in one pull request, add it under
  the new path in the next (or move the state by hand first).
- Deleting a unit destroys its resources; the plan shows it, the apply waits
  for a manual run with `allow_destroy`.
- `import` blocks bring existing resources under code; they are removed in a
  follow-up pull request once applied.

## Locally

Only `terragrunt hcl fmt`, `terragrunt hcl validate` and `plan`
(`terragrunt run --all plan` or in one unit). Never `apply` or `destroy` -
the settings deny them, and the pipeline is the only writer.

## Checks

`terragrunt hcl fmt --check`, `hcl validate`, `conventions / check` (rules in
`.github/conventions.yaml`: module from git, dependencies in `_common/deps`, mocks,
tags, no hard-coded IDs or regions, no `_envcommon`), Sentinel lint where
Sentinel content lives, trivy over the plan, and the plan itself.
