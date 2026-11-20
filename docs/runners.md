# Runners

Where the jobs of the shared workflows run. GitHub's hosted runners by
default; one self-hosted runner on the home NAS for the jobs that have to be
in the home network. The runner itself (image, launcher, GitHub App,
hardening) is described in
[`bearclave/app-nas`](https://github.com/bearclave/app-nas/blob/main/docs/runner.md).

## What runs where

| Job | Runner | Why |
|---|---|---|
| `terragrunt-home.yml` plan (`plan-shard`) and `apply` of `iac-home` | NAS: `[self-hosted, nas]` (`network-runner`) | the router's REST API is in the home network; without the WireGuard tunnel there is no VPN peer of the runners and no tunnel to come up |
| `container.yml` `deploy` of `app-home` (staging, prod) | NAS: `[self-hosted, nas]` (`deploy-runner`) | the app runs on the NAS; the job reaches it over SSH on the LAN and smoke-tests it through the router's reverse proxy |
| Everything else of those repositories: `security`, `pr`, `changes`, `select`, `validate`, the plan and apply comments, `verify`, `build`, `publish`, `retag`, `gate` | GitHub, `ubuntu-24.04` | needs nothing at home; on the NAS it would only take memory and time from the jobs that do |
| Every other repository (Tier-0, `iac-azure`, `iac-identity`, `iac-github`, the Microsoft cloud, modules, websites, `ci`) | GitHub, `ubuntu-24.04` | no reason to be at home, and a runner group admits only `iac-home`, `app-home` and `app-nas` |

`app-nas` is in the runner group so that a job of its own (a deployment of
the NAS services) can use the runner later; today its pipeline runs on GitHub.

## The rules

- **One runner group, `nas`**, visible to `iac-home`, `app-home` and `app-nas`
  only, never to public repositories (`iac-github`, `runner-groups`). The
  organisation allows self-hosted runners in the same three repositories
  (`iac-github` `docs/manual-steps.md`). A job of any other repository that
  asks for `nas` finds no runner, waits in the queue until GitHub gives up
  on it, and never runs at home.
- **Ephemeral.** Each runner registers for one job (a just-in-time
  configuration) in a fresh container and is gone when the job ends: nothing
  a job leaves behind reaches the next one.
- **No Docker socket, no secret in the runner.** The job gets a container of
  the official image `ghcr.io/actions/actions-runner`; the GitHub App key that
  registers runners stays with the launcher on the NAS.
- **Only the job that needs home runs there**, named in the shared workflow;
  a caller in a repository never writes `runs-on`.
- **Security scans and pull request checks stay on GitHub**: they are cheap
  there and need nothing at home.
- **One job at a time.** The NAS has 2 GB of memory: the launcher starts one
  runner, so jobs queue. `iac-home` never overlaps anyway (its concurrency
  group); a deployment of `app-home` waits for a running plan.

## What a job on the NAS has

The image brings bash, coreutils, curl, jq, git, unzip, openssh-client,
python3 (3.12) and sudo, the same Ubuntu 24.04 as `ubuntu-24.04`. What it
lacks a job installs: the Azure CLI at a pinned version
(`templates/_fragments/self-hosted-tools.yml`, about a minute), Terraform,
Terragrunt and trivy as on GitHub (`tool-versions.env` of the caller).
`actions/cache` and artifacts work as on GitHub.

The network position is the NAS's in VLAN trusted: the router's management
(`management` address list), the LAN and the internet. The router's resolver
gives the internal names (`mikrotik.bearclave.cz`, `byt.bearclave.cz`).

## When the NAS is down

- `iac-home`: the caller drops `network-runner` and `vpn-script`, and plan
  and apply run on GitHub through WireGuard again (peer `ci`, kept for this).
  A pull request with that change, or a manual run of a branch with it, is
  the way back; nothing else waits on the NAS.
- `app-home`: the app is down with the NAS anyway; a deployment waits in the
  queue until the runner is back, or is cancelled and run again later.

## Keeping it current

| What | Where | Who |
|---|---|---|
| Runner image (digest) | `app-nas` `deploy/runner/compose.yaml` | Dependabot (`docker-compose`), monthly; the owner deploys it. GitHub stops giving jobs to a runner 63 days after a newer runner release - well inside the monthly bump |
| Azure CLI | `templates/_fragments/self-hosted-tools.yml` here | by hand, with a tool bump (deps.yml does not read it) |
