# Workflows for the whole organisation

The workflows of this repository that act on other repositories, all as the
GitHub App `bearclave-bot`: each job signs in to Azure by OIDC on its own
environment, reads the App's key from `kv-githubbot-sec-weu-001`
(`src/github_bot.py`) and narrows the installation token to what it needs.
No secret in GitHub. The identities: [identities.md](identities.md).

| Workflow | When | Environment, identity | What |
|---|---|---|---|
| `deps.yml` (`src/deps.py`) | Mondays (actions), the 2nd of the month (tools, providers) | `deps`, `sp-ci-deps` | one `deps` pull request per repository and scope |
| `ci-pins.yml` (`src/ci_pins.py`) | a published release | `deps`, `sp-ci-deps` | moves the `bearclave/ci@vX.Y.Z` pins of every repository to the new release |
| `major-tag.yml` (`src/major_tag.py`) | a published release | `major-tag`, `sp-ci-major-tag` | moves `v2` to the release |
| `project-sync.yml` (`src/project_sync.py`) | daily 06:15 UTC | `project-sync`, `sp-project-sync` | adds every open issue and pull request to the Project of its repository's group |
| `housekeeping.yml` (`src/housekeeping.py`) | Mondays | `project-sync`, `sp-project-sync` | the stale branches and pull requests of every repository, in one issue per repository |

A manual run of `deps.yml`, `ci-pins.yml` and `housekeeping.yml` is a dry run
by default. The pull requests and issues are assigned to `vars.OWNER` (the
organisation variable); without it to nobody.

## deps.yml

Opens one `deps` pull request per repository and scope, from `deps/tools` or
`deps/actions`, labelled `type:deps`, assigned to the owner, who approves and
merges it:

| Scope | When | What |
|---|---|---|
| `actions` | Mondays | the actions pinned in `templates/` and in this repository's own workflows, then `tools/build.py` |
| `tools` | the 2nd of the month | `.github/tool-versions.env` of every repository (version and sha256), the security tools in `templates/security.yml`, the providers pinned in `root.hcl`/`account.hcl` of the Terragrunt repositories; the lower bounds of the module repositories only in the run summary |

A version is proposed when it is a release at least 7 days old and of the
same major; a newer major is only listed. A pull request still open on the
branch is left alone. `iac-m365` (being retired) is skipped. The
markdownlint image of `pr.yml` (version and digest) is bumped by hand.

It replaces Dependabot in this repository (#12): Dependabot bumped the
generated workflows, not `templates/`. Dependabot security updates stay on.

```bash
gh workflow run deps.yml -f scope=all                  # dry run
GH_TOKEN=$(gh auth token) python3 src/deps.py --dry-run --scope tools --repo iac-azure   # locally
```

## ci-pins.yml

Every repository whose `.github/workflows/` pins `bearclave/ci/...@vX.Y.Z` to
an older release of the same major gets a pull request from `deps/ci`, or its
open one is moved to the new release; the owner merges it after its plan.
`@v2` callers, other majors and `iac-m365` are left alone.

```bash
gh workflow run ci-pins.yml -f release=v2.5.0          # dry run
GH_TOKEN=$(gh auth token) python3 src/ci_pins.py --dry-run v2.5.0
```

## major-tag.yml

Moves `v2` only to a published, non-pre-release `vX.Y.Z` with a verified
signed tag that is the newest of its major, never backwards. `v2` becomes a
lightweight ref to the release's commit. The `major-tags` ruleset lets only an
organisation owner and `bearclave-bot` move it.

## project-sync.yml

Moved from `ops-tools` unchanged in behaviour. The group is the repository's
custom property `group`, read on every run, so a new repository needs nothing:

| `group` | Project |
|---|---|
| `iac` | 1 IaC |
| `app` | 2 Apps |
| `template` | 3 Templates & content |
| `platform` | 4 Platform |

Archived repositories and repositories without a group are skipped; items
already in a Project stay and nothing is removed. A group with no Project
fails the run. The token: Projects write, issues, pull requests and metadata
read.

```bash
AZURE_CONFIG_DIR=~/.azure-bearclave-matej python3 src/project_sync.py --dry-run
```

## housekeeping.yml

One run for the organisation instead of a caller in every repository
(governance `workflow.md#housekeeping`): in every repository that is not
archived and has issues, branches without a pull request over 30 days and
pull requests without an update over 14 days (`status:blocked` listed apart),
in one issue of that repository (type Debt) that every run updates and that
closes itself once nothing is stale. It deletes nothing.

```bash
GH_TOKEN=$(gh auth token) python3 src/housekeeping.py --repo bearclave/ci --dry-run
```

## bot-gh

`tools/bot-gh.py` runs `gh` as `bearclave-bot` on the owner's machine, so
that the agents' pull requests are not the owner's own and he can approve
them. The commits stay the owner's and signed: push the branch with git, then
open the pull request through it. It reads the App's key with the current
`az` sign-in (`AZURE_CONFIG_DIR`, default `~/.azure-bearclave-matej`; reader
group `sg-azure-vault-githubbot-readers`), makes a JWT, exchanges it for an
installation token (one hour) and runs `gh` with `GH_TOKEN`. Nothing is
printed.

```bash
ln -sf ~/Repositories/Bearclave.GitHub/platform/ci/tools/bot-gh.py ~/.local/bin/bot-gh   # once

git push -u origin feat/my-change
bot-gh pr create --draft --head feat/my-change --title "feat(x): ..." --body-file /tmp/pr.md
```

`pr / check` assigns a pull request of the App to the owner.
