---
paths:
  - "**/*.hcl"
  - "infrastructure/**/*.yaml"
---

# Terragrunt configuration

<!-- Standard of bearclave/governance (standards/code/terragrunt.md). In other repositories this is a managed copy - change it in governance. -->

Applies to the live repositories `iac-azure`, `iac-github`, `iac-home`,
`template-iac-terragrunt` and the repositories of the Microsoft cloud:
`iac-identity`, `iac-conditional-access`, `iac-intune`, `iac-exchange`,
`iac-purview`, `iac-sentinel`. What such a repository must have:
[repo-types.md](https://github.com/bearclave/governance/blob/main/standards/repo-types.md). Modules: [terraform.md](https://github.com/bearclave/governance/blob/main/standards/code/terraform.md);
the Azure hierarchy, naming and tags: [azure.md](https://github.com/bearclave/governance/blob/main/standards/code/azure.md); Entra ID and
Microsoft 365: [m365.md](https://github.com/bearclave/governance/blob/main/standards/code/m365.md); Sentinel: [sentinel.md](https://github.com/bearclave/governance/blob/main/standards/code/sentinel.md).
Skeleton: [../templates/terragrunt/](https://github.com/bearclave/governance/tree/main/standards/templates/terragrunt/).

## Root

| Entry | Holds |
|---|---|
| `README.md`, `.gitignore`, `.gitattributes`, `.editorconfig`, `.claude/`, `.github/` | as every repository ([layout.md](https://github.com/bearclave/governance/blob/main/standards/layout.md)) |
| `root.hcl` | mechanics only: `remote_state`, `generate` (providers, versions), the inputs of the context; no literal names or IDs |
| `account.hcl` | the data of the repository: tenant, state, `modules` + `modules_ref`, provider versions (`versions`), identities, default region |
| `_common/` | shared HCL (section `_common/` below) |
| `docs/` | `setup.md` (bootstrap as a procedure with the commands), `manual-steps.md`, `out-of-scope.md`; one page per collection, named after its unit folder (`docs/analytic-rules.md`) |
| `infrastructure/` | every unit |
| `.github/schemas/` | the JSON Schema of each collection's module, `<module>.schema.json` ([Collections](#collections)) |

No `tools/` and no `deploy/`: a script the pipeline runs lives in
`bearclave/ci`, a script a person runs in the know-how repository, and a
bootstrap is a procedure in `docs/setup.md`, never code. The layout check
refuses them ([layout.md](https://github.com/bearclave/governance/blob/main/standards/layout.md#folders)).

## State

The state key is the unit's path without `infrastructure/`:

```hcl
remote_state {
  backend = "azurerm"
  config = {
    # ...
    key = "${trimprefix(path_relative_to_include(), "infrastructure/")}/terraform.tfstate"
  }
}
```

- One state per unit; the `remote_state` block in every repository (no
  backend template, no local-state switch in code; a local state is a
  procedure in `docs/setup.md`).
- `.terraform.lock.hcl` committed next to `terragrunt.hcl`, hashes for
  `linux_amd64` and `darwin_arm64` ([toolchain.md](https://github.com/bearclave/governance/blob/main/standards/toolchain.md#terraform-providers)).
- Deleting a unit destroys its resources; `destroy / approved` needs the
  owner's approval after the plan ([workflow.md](https://github.com/bearclave/governance/blob/main/standards/workflow.md#review)).
- `import` blocks bring existing resources under code and are removed in a
  follow-up pull request once applied.

### Handing a unit over to another repository

What moves between repositories (from `iac-azure` `tenant/` or `iac-m365`
into a new repository) never changes the environment:

1. The new repository imports it: `import` blocks, a plan that only imports
   and changes nothing. A difference is fixed in the code, never in the
   tenant.
2. After that apply the old repository forgets it: its unit gets
   `removed { from = <address>  lifecycle { destroy = false } }` for
   every resource (generated in the unit, against the empty module of
   `iac-modules`), and its plan shows them forgotten, nothing destroyed.
3. A later pull request deletes the emptied unit (its state is empty, the
   destroy has nothing to do); the owner deletes the old state blob.

Between steps 1 and 2 nothing else merges in either repository for that
domain, and the overlap never spans a weekend (the weekly drift).

### Moving a unit

A unit whose state key changes needs its state moved first. The pipeline
plans the new path and does not destroy the old one only when the move is
listed; otherwise it refuses the move.

| Change | What the owner does before the merge | `.github/state-moves.yaml` |
|---|---|---|
| into `infrastructure/`, rest of the path the same | nothing (same key) | - |
| rename to a new path | copy the blob `<from>/terraform.tfstate` to `<to>/terraform.tfstate` | entry |
| merge into an existing unit | `import` blocks in `to`, `terraform state rm` in `from` | entry |

```yaml
# .github/state-moves.yaml - state keys, without infrastructure/ and the file name
moves:
  - from: mg-corp/policy-assignments
    to: mg-corp/global/policy-assignments
```

The plan of a moved unit may create, destroy or replace nothing; a create
means the state did not come along and the plan job fails. The pull request
carries the owner's commands (read-only for an agent). Stale entries are
removed in a later pull request, the old blob after a green apply.

## `infrastructure/`

The directory hierarchy is the context: `root.hcl` reads the context files on
the way up and derives naming, tags, location, provider and state key.

| Repository | Under `infrastructure/` |
|---|---|
| `iac-azure` | `mg-<name>/` ... `sub-<alias>/` ... `<region>/` ... `rg-<workload>/` ([azure.md](https://github.com/bearclave/governance/blob/main/standards/code/azure.md#hierarchy)) |
| `iac-github` | `organization/`, `rulesets/`, `teams/`, `repositories/<repository>/` |
| `iac-home` | `router/`, `access-point/`, each with `device.hcl` and one unit per function (`dns/`, `firewall/`, `wifi/`) |
| `iac-identity`, `iac-conditional-access`, `iac-intune`, `iac-exchange`, `iac-purview` | collection units, flat or under a category folder that holds at least two units (`applications/`, `policies/`, `mail-flow/`); trees in [m365.md](https://github.com/bearclave/governance/blob/main/standards/code/m365.md#repositories) |
| `iac-sentinel` | `<workspace>/` (the name of the Log Analytics workspace) with `workspace.hcl`, the workspace's own unit and its collection units ([sentinel.md](https://github.com/bearclave/governance/blob/main/standards/code/sentinel.md#layout)) |

### Context files

A context file holds only what its folder name does not say.

| File | Level | Exact content | From the folder |
|---|---|---|---|
| `mg.hcl` | management group | `display_name` | `name` (folder without `mg-`) |
| `subscription.hcl` | subscription | `subscription_id`, `scope`, `entity`, `environment`, `owner`, `billing = { profile, invoice_section }` | `alias` (the folder) |
| `region.hcl` | region | `location` | `code` (the folder, `weu`) |
| `rg.hcl` | resource group | `criticality`, `data_classification`; `owner` only to override the subscription's | `workload` (folder without `rg-`) |
| `device.hcl` | device (`iac-home`) | `name`, `model`, `routeros`, `host` (key in `_common/network.hcl`), `managed`, `secret_prefix`, `ca_bundle` (heredoc) | - |
| `workspace.hcl` | Sentinel workspace (`iac-sentinel`) | `subscription_id`, `resource_group`, `playbooks_resource_group` | `name` (the folder) |

`global/` has no context file: `root.hcl` sees `global` in the path, the
region code is `global` and the location the default region of
`account.hcl`. `iac-github` has no context file (one organisation, in
`account.hcl`).

### Folder dictionary

**A unit folder is named after its module** ([terraform.md](https://github.com/bearclave/governance/blob/main/standards/code/terraform.md#layout-of-iac-modules)):
`key-vault/` runs `azure/key-vault`. Several instances in one scope:
`<module>-<purpose>` (`data-collection-rule-syslog-home/`). A folder named in
the plural holds one unit per instance, named after the instance
(`repositories/iac-home/`, `logic-apps-standard/la-soar/`). A collection unit
may leave out the platform word of its module, set as `prefixes` of the
`folder-dictionary` rule (`analytic-rules/` runs `azure/sentinel-analytic-rules`). The scope's own unit sits in the folder of the
scope: `mg-*/`, `sub-*/`, `rg-*/` ([azure.md](https://github.com/bearclave/governance/blob/main/standards/code/azure.md#hierarchy)); `root.hcl`
keeps the units below such a unit out of its cache copy:

```hcl
terraform {
  exclude_from_copy = distinct([for f in fileset(get_terragrunt_dir(), "*/**/terragrunt.hcl") : "${split("/", f)[0]}/**"])
}
```

**One unit per resource with its children**: a key vault with its
certificates, a storage account with its containers and their access, one
`data-collection` unit with the endpoints and rules of the scope. A unit
never holds another unit below a resource.

### Files in a unit

| File | Rule |
|---|---|
| `terragrunt.hcl`, `.terraform.lock.hcl` | always |
| other `*.hcl` | context files of the level, `imports.hcl` of a first apply |
| `<key>.yaml`, also in subfolders | the objects of a collection unit, read with `fileset()` by its `_common/units/<module>.hcl` ([Collections](#collections)) |
| `definition.json` | a Logic App (Consumption) workflow, the code view of the designer as it is, read with `file()` |
| `workflows/<name>/workflow.json` | the workflows of a Logic App (Standard), read with `fileset()` |
| anything else | not allowed: KQL inline (a heredoc, or `query: \|` in YAML), a CA bundle as a heredoc in `device.hcl`, a public certificate only where no other way exists (exception with a reason); never `*.yml`, `*.kql`, `*.csv`, ARM or Bicep |
| private key, `.pem`, `.pfx` | never in the repository |

## Collections

The repositories of the Microsoft cloud manage many objects of one type
(policies, rules, groups, applications), edited by people who need not know
Terraform. Their units are collections:

| # | Rule |
|---|---|
| 1 | **A unit is a collection of one type**: one module, one state, one lock file. Never a unit per object (every new object would need a unit and a lock file), never one unit for everything. |
| 2 | **One object is one file `<key>.yaml`** in the unit or below it. The file name is the key (`for_each`, the address in the plan): lowercase ASCII words joined by `-`, unique in the unit. The display name is inside the file. |
| 3 | **Subfolders are for people only**: by log source, platform, persona (`analytic-rules/entra-id/`). Terraform does not see them; moving a file between them changes nothing. Renaming a file changes the key: destroy and create. |
| 4 | **A setting of the tenant** (authorization policy, organisation config) is a unit with one file, `settings.yaml`. |
| 5 | **Children live in the parent's file**: an application with its service principal, federated credentials, certificate and API permissions; a policy with its rule; a group with its members; a Logic App with the roles of its identity. |
| 6 | **A host with its own price, runtime and identity** (a Logic App Standard) is a unit per instance, as in `iac-azure`; what runs on it is files in the unit (`workflows/<name>/workflow.json`). |
| 7 | **Split a collection** only for another identity, another blast radius, another dependency, or past ~300 objects - decided up front, because moving an object between units moves its state. |
| 8 | **References**: inside the repository by the key (`playbook: pb-entra-revoke-sessions`), across repositories by name (`group:sg-ca-exclude-breakglass`, `sp:sp-tf-sentinel`), never by an ID. No user, device or mailbox and no object ID of one in YAML; a user appears only as a member of a group, by UPN. Allowed: IDs that never change (the application ID of a first- or third-party application, a role template ID). |
| 9 | **The format exposes the product**: every setting the API has (every Conditional Access condition and control, the colour of a label), with Microsoft's names where Microsoft has a file format (analytic rules). What the module cannot set yet is a gap in its README, not a missing key. |
| 10 | **Each collection is documented** in `docs/<unit folder>.md`: what one file manages, every key with its values, an example, what is out of code. |

The unit is the canonical one with nothing to add: `inputs = {}`. The
`_common/units/<module>.hcl` of the collection reads the files and hands the
module a typed `map(object)`; the module reads nothing from disk, so the plan
shows the values and a module test needs no files:

```hcl
# _common/units/sentinel-analytic-rules.hcl
locals {
  ctx = read_terragrunt_config("${get_repo_root()}/_common/context.hcl").locals
  dir = get_terragrunt_dir()
  items = {
    for f in fileset(local.dir, "**/*.yaml") :
    trimsuffix(basename(f), ".yaml") => yamldecode(file("${local.dir}/${f}"))
  }
}

terraform {
  source = "${local.ctx.modules}//azure/sentinel-analytic-rules?ref=${local.ctx.modules_ref}"
}

inputs = {
  rules = local.items
}
```

### Schemas

Terraform drops a key it does not know when it converts YAML to a typed
object, so a typo would pass the plan. Every collection is therefore
checked against a JSON Schema in `pr / check` of every pull request
(`collection-schema`, [pipelines.md](https://github.com/bearclave/governance/blob/main/standards/pipelines.md#checks-and-their-exceptions)):

| What | Rule |
|---|---|
| Source | the module: `iac-modules/<platform>/<module>/schema.json`, released with its tag, matching the module's variable |
| Copy in the live repository | `.github/schemas/<module>.schema.json`, byte for byte the module's file at the `modules_ref` the repository pins, updated in the pull request that moves `modules_ref`; the check reads this copy |
| Dialect | JSON Schema 2020-12, the subset the validator of `ci` knows (`src/schema_check.py`); another keyword fails the check |
| Closed | every object has `additionalProperties: false`, or a schema for the values of a map whose keys are data |
| Editor | `.vscode/settings.json` maps the globs of a collection to its schema (`yaml.schemas`), so VS Code completes and underlines |
| Catalogue | a repository without units (`content-sentinel`) lists its collection folders in `.github/conventions.yaml` (`collection-schema: collections:`) |

## `_common/`

| Path | Holds |
|---|---|
| `_common/context.hcl` | the context derived from `account.hcl` and the shared data, read by `root.hcl` and by `_common/units/*` as `local.ctx` |
| `_common/units/<module>.hcl` | the shared part of every unit of one module: `terraform { source }`, the values identical in **every** unit of the module, the dependency on the parent |
| `_common/deps/<name>.hcl` | a shared `dependency` block with mock outputs, for units that read another unit's outputs |
| `_common/mocks.hcl` | the commands mocks are allowed in (never `apply`, `destroy`) |
| `_common/templates/*.tf.tmpl` | what `root.hcl` generates (providers, versions) |
| `_common/<topic>.hcl` | shared data: `naming.hcl`, `tags.hcl` (allowed values), `standard.hcl` (github), `network.hcl` (home) |

Rules of `_common/units/`:

- Named exactly after the module (`units/key-vault.hcl` for
  `azure/key-vault`); one per module the repository uses, and a unit never
  has its own `terraform` block.
- Only what is **100 % identical** in every unit of the module: the source,
  the security baseline that may not be left out (purge protection,
  `shared_access_key_enabled = false`). SKU, retention, soft delete, rules,
  network ACLs and anything an instance may differ in stay in the unit.
- Deep merge joins lists and merges maps: a list a unit replaces is never in
  `units/`.
- Source from git with the ref of `account.hcl`:
  `source = "${local.ctx.modules}//azure/key-vault?ref=${local.ctx.modules_ref}"`.
  Never a local path; locally `--source-map` or `TG_SOURCE_MAP`.
- A file in `_common/` is read by at least two units; otherwise it belongs to
  the unit.

## Canonical unit

| # | Block | Required |
|---|---|---|
| 1 | `include "root"` (`expose = true`) | always |
| 2 | `include "unit"` -> `_common/units/<module>.hcl` (`merge_strategy = "deep"`) | always |
| 3 | `include "<dependency>"` -> `_common/deps/<name>.hcl` | when the unit reads another unit's outputs |
| 4 | `dependencies` | only an order that is not in `units/` |
| 5 | `locals` | rarely: a literal repeated inside the unit |
| 6 | `inputs` | always, possibly `{}`: what is specific to this instance |

```hcl
include "root" {
  path   = find_in_parent_folders("root.hcl")
  expose = true
}

include "unit" {
  path           = "${get_repo_root()}/_common/units/key-vault.hcl"
  merge_strategy = "deep"
}

inputs = {
  sku_name                   = "standard"
  soft_delete_retention_days = 90
  lock                       = "CanNotDelete"
  role_assignments           = {}
}
```

- Every input of the module is set, in the unit or in `units/` - the
  modules have no defaults.
- No name, region, tag or ID written out: they come from the context, a
  dependency or `account.hcl`.
- No header comment: the path says what the unit manages. A comment only at a
  value that is an exception (`lock = "none"` in a high-criticality resource
  group needs one) - [documentation.md](https://github.com/bearclave/governance/blob/main/standards/documentation.md#comments-in-code).

## Locally

Only `terragrunt hcl fmt`, `terragrunt hcl validate` and `plan`
(`terragrunt run --all plan` or in one unit), against the local modules with
`--source-map` and `-lock=false`. Never `apply`, `destroy` or a `state`
command - the settings deny them, and the pipeline is the only writer.

## Checks

`terragrunt hcl fmt --check`, `hcl validate`, `conventions / check`
(`.github/conventions.yaml`), trivy over the plan, and the plan itself:

| Rule | Holds |
|---|---|
| `module-source` | the source from git with `modules_ref`, in `_common/units/` |
| `dependencies-in-common`, `mock-outputs` | `dependency` blocks only in `_common/deps/` and `_common/units/`, with mocks that never reach `apply` |
| `tags-required` | context files set their tags with an allowed value |
| `no-hardcoded-ids`, `no-hardcoded-location` | no ID or region written out in a unit |
| `unit-assets` | no file in the unit tree but HCL and what [Files in a unit](#files-in-a-unit) allows, each read by its unit |
| `collection-schema` | every `<key>.yaml` matches the schema of its module, keys unique and well formed (in `pr / check`) |
| `scope-layout` | what each level of the hierarchy may hold ([azure.md](https://github.com/bearclave/governance/blob/main/standards/code/azure.md#hierarchy)) |
| `folder-dictionary` | a unit folder named after its module (`prefixes` for collections) |
| `lock-none-needs-reason` | `lock = "none"` in a high-criticality group has a comment |

The pipeline plans only the units a change touches: a file of
`_common/units/` or `_common/deps/` plans the units that include it, a
context file the units below it, a `<key>.yaml` its collection.
