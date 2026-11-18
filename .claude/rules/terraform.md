---
paths:
  - "**/*.tf"
  - "**/.tflint.hcl"
---

# Terraform modules

<!-- Standard of bearclave/governance (standards/code/terraform.md). In other repositories this is a managed copy - change it in governance. -->

Modules live in `bearclave/iac-modules`, grouped by domain (`platform/`,
`identity/`, `security/`, `sentinel/`, `github/`, ...); shared building
blocks in `_shared/`. Skeleton: [../templates/terraform-module/](https://github.com/bearclave/governance/tree/main/standards/templates/terraform-module/).

## The rule without exception

**No variable has a `default`**, and no `optional(type, default)`. Every
unit carries the complete settings of its resource; a module that fills
something in quietly creates a setting nobody wrote. Opting out is an
explicit value (`{}`, `[]`, `"none"`, `null` where the variable is
`nullable`).

## Files of a module

| File | Content |
|---|---|
| `versions.tf` | `required_version` and providers with a **range** (`>= 4.0`); the exact version is pinned by the consuming repository's `root.hcl` |
| `variables.tf` | every input with `type` and `description`; `validation` blocks for values the API would reject late |
| `main.tf` | the resources; split into `<concern>.tf` when it passes ~300 lines |
| `outputs.tf` | every output with `description`; IDs and names a unit needs, never secrets unless `sensitive = true` |
| `README.md` | what it manages, why it is built this way, an example input, the gaps of the provider |

## Style

- `terraform fmt`; names `snake_case`; resource names describe the role,
  `this` for the single main resource.
- Collections as `map(object)` keyed by a stable name, iterated with
  `for_each` - never `count` for things that have identity.
- Every resource that supports them takes `diagnostics`, `alerts`, `lock`
  and `role_assignments` through the `_shared/` modules.
- Principals by display name (`group:`, `user:`, `sp:`), resolved by
  `_shared/principals`; `id:` only for one the same change creates.
- `lifecycle.ignore_changes` only with a comment saying which external
  process owns the attribute.
- A gap of the provider (an attribute it does not know) is written next to
  the variable it concerns, with the version it was seen in.
- No provider block and no backend in a module.

## Checks

`terraform fmt -check`, `terraform validate` per module, `tflint` (azurerm
ruleset), `trivy config` (HIGH and CRITICAL fail; an accepted finding goes
into `.trivyignore.yaml` with the reason) and `conventions / check`
(`variable-no-default`, `variable-documented`).

## Changing a module

A module change is its own pull request, merged before the units that use
it. Name the affected consumers in the Impact section. A removed or renamed
input is a breaking change (`feat(<module>)!:`).
