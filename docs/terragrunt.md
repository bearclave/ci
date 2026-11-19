# Terragrunt pipeline

`terragrunt.yml` (`iac-azure`, `iac-github`) and `terragrunt-home.yml`
(`iac-home`) run the same jobs, `templates/_fragments/terragrunt-jobs.yml`;
the home variant only changes the defaults (below). The checks are
`pipeline / <job>`; only `pipeline / gate` is required, and it needs all of
them.

```text
changes -> select -> validate ----------------------------\
                  \-> plan (1 of n) .. plan (n of n) -> plan -> destroy / approved -> apply -> verify
                                                                                            \-> report
                                                                                     gate (always)
```

## Identities

| Job | Identity | Federated on |
|---|---|---|
| validate (only with `validate-sign-in`), plan, drift | `client-id-plan`, read-only (`AZURE_CLIENT_ID_PLAN`) | `pull_request`, `ref:refs/heads/main` |
| apply (with its verification) | `client-id-apply` (`AZURE_CLIENT_ID_APPLY`) | `environment:<environment>` only |

Every plan, the one on `main` included, runs as the plan identity: the
identity that writes is reachable from the apply job's environment and from
nowhere else. `iac-github` signs in to GitHub the same way: the read-only
App (`github-app-vault-plan`) for every plan, the App with write access
(`github-app-vault-apply`) for the apply. The inventory is
[identities.md](identities.md).

## changes

On a pull request, whether anything can change a plan
(`src/terragrunt/changes.py`): a path has to match the caller's
`relevant-paths` and not be one that never reaches a plan - `.claude/**`,
Markdown outside every unit, and everything under `.github/` except the
workflows, `tool-versions.env` and `trivyignore.yaml`. Otherwise the rest is
skipped and the gate passes.

## select

Seconds, no sign-in: the base commit, the unit selection (the one plan and
apply use) and its split into the plan jobs (`src/terragrunt/select-units.py`).

- A pull request is compared with its base; `main` with the head of the last
  successful run on `main`, so the units of a failed or held-back apply stay
  selected until they are applied.
- `root.hcl` and `account.hcl` select the whole tree.
- A file under `_common/` selects the units that include or read it, as
  `terragrunt find --reading --include` reports them
  (`_common/units/key-vault.hcl` plans the key vaults, `_common/deps/groups.hcl`
  the units that read the groups). A file no unit reports - a template
  `root.hcl` renders, a deleted file - selects the whole tree.
- A context file (`mg.hcl`, `subscription.hcl`, `region.hcl`, `rg.hcl`,
  `device.hcl`) selects the units at and below it and any other unit that
  reads it.
- Any other file selects its nearest unit, with everything that unit depends
  on.
- A file outside every unit (the workflows, the scripts) selects the whole
  tree, except what never reaches a plan (the list of changes above): a push
  of a README, an agent rule or `.github/conventions.yaml` plans nothing.
- A deleted unit is planned and applied as a destroy from the base commit.
- A moved unit is planned under its new path when its state came along
  ([Moving a unit](#moving-a-unit)); otherwise the run fails.
- A scheduled run selects the whole tree.

Units live under `infrastructure/`; the state key, and every path the summary
and the comments show, is the path without that prefix.

## Moving a unit

The state key is the unit's path without `infrastructure/`
(`trimprefix(path_relative_to_include(), "infrastructure/")` in `root.hcl`).

| Move | State | What the pipeline does |
|---|---|---|
| into `infrastructure/`, the rest of the path the same | same key | plans the new path; nothing to do |
| a new path (rename) | the owner copies the blob `<from>/terraform.tfstate` to `<to>/terraform.tfstate` before the merge | plans `to`, does not destroy `from` |
| into an existing unit (merge) | the owner moves the resources into the state of `to` (`import` blocks in `to`, `terraform state rm` in `from`) | plans `to`, does not destroy `from` |
| anything else | - | fails: delete in one pull request, add in the next |

A rename or a merge is listed in `.github/state-moves.yaml`, keys without
`infrastructure/` and without the file name:

```yaml
moves:
  - from: mg-corp/policy-assignments
    to: mg-corp/global/policy-assignments
  - from: mg-corp/mg-corp-platform/sub-mgmt-prod/weu/rg-security/key-vault/certificate
    to: mg-corp/mg-corp-platform/sub-mgmt-prod/weu/rg-security/key-vault
```

Each `from` once, `to` a unit of the head commit; an entry whose `from` is no
unit gone in the change is ignored (it is stale after the merge and goes in a
later pull request). The plan of every moved unit may create, destroy or
replace nothing (`src/terragrunt/check-moves.py`): a create means the state
did not come along, and the plan job fails before anything is applied. The
old blob stays until the owner deletes it after the apply.

## validate

Next to the plan jobs, not before them (terraform validate takes minutes on
two cores); the plan comment, apply and the gate wait for it.

| Step | Fails when |
|---|---|
| Principal references (select) | a principal the change creates is referenced by display name (`principal-refs`) |
| HCL formatting | `terragrunt hcl fmt --check` finds an unformatted file |
| HCL validation | `terragrunt hcl validate` cannot read a file |
| Terraform validate | `terraform validate` (init with `-backend=false`) fails in a selected unit; dependencies answer with their mock outputs for validate |
| Lock files | a selected unit has no committed `.terraform.lock.hcl` (a warning with `require-lock-files: false`), or init had to change a committed one |

`validate-sign-in` signs in as the plan identity first, for a configuration
that reads a secret while it is parsed (`iac-home`: the router's password by
`run_cmd`).

## plan

A plan is bound by the runner's CPU, so select splits a large selection into
shares of up to 20 units, at most `plan-shards` (default 3; behind a VPN
always 1). Each `plan (k of n)` job plans its share in dependency order and
passes on `plan.txt` and what the comment shows of its JSON plans
(`plan-summary.py --export`); the JSON plans themselves, which hold sensitive
values in clear text, never leave the job. `plan` reads the shares back
(`--import`) into one summary, comment and verdict. The whole tree of
`iac-azure` (95 units) plans in under 4 minutes.

A plan on a pull request does not lock the state (a cancelled run would leave
its lock behind); the plan on `main` does. Locks a runner took more than 70
minutes ago are released by the first share.

## destroy / approved

GitHub Team has no approval on an environment of a private repository, so
the approval of destroys and replaces is the owner's review of the pull
request, given after its plan (`src/terragrunt/destroy-approval.py`). On a
pull request the check passes when the plan has none, or when such a review
exists; a review submitted later re-runs it (`destroy-review.yml`). On `main`
it tells the apply whether the merged pull request passed it on an approval.

## apply

From `main` only, in the environment, under the identity that writes, when
validate passed and the plan proposes something and destroys nothing (or the
destroys were approved, or a manual run passed `allow-destroy`).

The apply plans the units again instead of applying the saved plan of the
plan job, for three reasons: a unit reads the outputs of the units it depends
on, which change in the same apply (a saved plan carries the old ones); the
plan is split across jobs; and a saved plan holds every sensitive value in
clear text, which would have to travel as an artifact. What keeps the apply
honest instead: the plan of the same run, minutes before, decides whether it
runs; destroys need the approval; and the verification right after it fails
the run when the result differs from the code.

**Verification** runs in the apply job:

1. *Plan again* - the applied units with `-detailed-exitcode`: anything but
   "no changes" fails.
2. *Failed Azure operations* - `az monitor activity-log list --status Failed`
   in the subscriptions of the applied units' azurerm providers (and
   `subscription-id`), from a minute before the apply, filtered to the apply
   identity. Skipped without an azurerm provider; a log that cannot be read
   is `unreadable`, not a failure.
3. *Check blocks* - every `check {}` that failed, from the second plan and the
   apply log.

The result is `verification.md` (a `| Check | Result | Detail |` table) and
`verification.json` (version 1: `result`, `commit`, `run_url`, `replan`,
`activity_log`, `checks`), in the job summary and the artifact
`verification-<run id>`.

**verify** is the only job that writes to the repository (`contents`,
`pull-requests`, `issues: write`). When the verification failed it turns red
and, without rolling anything back, opens a `Bug` issue `Apply of <sha>
failed its verification` (`prio:high`, `source:drift`, assigned to
`vars.OWNER`) and, on a push, a draft pull request `revert/<short-sha>`.

## Plan and apply comments

Built from the plan in JSON by `src/terragrunt/plan-summary.py`,
`plan_details.py` and `apply-summary.py` (tests with sanitised real plans in
`tests/terragrunt/`).

**Plan comment**, one per pull request, rewritten on every push (marker
`terragrunt-plan`): the totals, a table of destroys and replaces, a table of
changes in fixed wording (``tag `owner` ``, ``setting `topics`: added `x` ``),
the status (security findings, formatting and validation) and what is needed
next; terraform's own diff per unit in `<details>`. Values known only after
apply, computed IDs, data sources and imports that change nothing are left
out; sensitive values never appear. Over GitHub's size limit the details are
shortened first, the tables last.

**Apply comment**, on the pull request merged as the applied commit, a new one
per run (marker `terragrunt-apply-<run id>`), from the `report` job: the rows
of the plan of the same run in the past tense with the result of each, the
errors and the verification. Another post-apply check adds its own table in
any `*.md` file of an artifact `verification-<run id>-<name>` uploaded by a
job the `report` job needs.

## gate

On a pull request: every job passed or was skipped. On `main`: select,
validate and plan passed, and an apply that was due ran and verified - a
skipped apply is red, so a merged change that never reached the tenant is
seen.

## Home network (terragrunt-home.yml)

| Input | Default there | Effect |
|---|---|---|
| `vpn-script` | `home-vpn.sh` | plan and apply join the home network (`up`, `down`) with `src/terragrunt/home-vpn.sh` of ci (a path in the repository still works); one plan job |
| `validate-sign-in` | `true` | validate signs in: the configuration reads the router's password while it is parsed |
| `renewal-unit` | `infrastructure/router/letsencrypt` | a scheduled run applies that unit when its plan proposes something (the certificate); a change anywhere else is drift and fails the run |
| `environment` | `home-prod` | the apply's environment |

The caller's concurrency group keeps every run apart: one WireGuard peer.

`home-vpn.sh up` joins every device whose `infrastructure/<device>/device.hcl`
says `managed = true`. It reads from the repository: peer `ci` and
`wireguard` (`endpoint`, `listen_port`), the device's `hosts` entry (address,
first name) and `webfig_port` in `_common/network.hcl`; `host` and the
heredoc `ca_bundle` in `device.hcl`; `key_vault` in `account.hcl` (secrets
`wg-key-ci`, `wg-psk-ci`); the server's key from the output `public_key` of
`infrastructure/router/wireguard`. Every device's REST API has to answer
before the plan.

A unit whose `exclude` block keeps it out of a plan (a device with
`managed = false`) is skipped by terragrunt and by the lock file check
(`excluded` of the selection), and fails nothing.

## Drift

`terragrunt-drift.yml` plans the whole tree of `iac-azure` and `iac-github`
once a week as the plan identity: anything it proposes, a failed check block
or a new trivy finding fails it. It catches the units that DEPEND on a
changed one, which the selection leaves out, and changes made outside the
code; Sentinel analytic rules in the workspace but not in code are reported.
