# Workflow: from the first line to `main`

<!-- Standard of bearclave/governance (standards/workflow.md). In other repositories this is a managed copy - change it in governance. -->

One task = one branch from a fresh `main` = one pull request = one squash
commit on `main`. Nobody pushes to `main`; the organisation rulesets in
`bearclave/iac-github` refuse it, for the owner too.

## Branches

```
<type>/<slug>
<type>/<issue>-<slug>
```

- `type` is one of the commit types below (`feat`, `fix`, `refactor`, ...).
- `slug`: lowercase `a-z`, `0-9` and `-`, 2-5 words, says what the branch does:
  `fix/dcr-syslog-priority`, `feat/42-ca-block-legacy-auth`.
- The issue number goes first when the branch resolves an issue.
- Always from a fresh `origin/main`: `git novy <type>/<slug>` fetches and
  branches in one step.
- Short-lived: merged or closed within days. A branch that waits on something
  stays a draft PR with the label `status:blocked` and a comment why.
- A worktree branch (`worktree-<name>`) is renamed to `<type>/<slug>` before
  its first push.
- Bot branches keep their own names: `dependabot/...`, `chore/tombstones`
  (weekly M365 drift).
- The `pr` check refuses any other branch name.

## Commits

[Conventional Commits 1.0](https://www.conventionalcommits.org/en/v1.0.0/),
template in [templates/commit-message.txt](https://github.com/bearclave/governance/blob/main/standards/templates/commit-message.txt):

```
<type>(<scope>)!: <summary>

<body: why the change is needed, what it does not do, wrapped at 72>

Refs #12
BREAKING CHANGE: <what callers must change>
```

| Type | Use for | Release effect |
|---|---|---|
| `feat` | new behaviour, resource, setting, page | minor |
| `fix` | wrong behaviour corrected, drift fixed | patch |
| `perf` | faster or cheaper, same behaviour | patch |
| `refactor` | structure changed, behaviour and plan unchanged | patch |
| `docs` | documentation and comments only | none |
| `test` | tests only | none |
| `ci` | workflows, pipeline scripts, repository tooling | none |
| `chore` | anything else that changes no behaviour (housekeeping, generated files) | none |
| `deps` | dependency, provider, action or tool version bump | patch |
| `revert` | reverts an earlier commit, names it in the body | patch |

- **Scope** is the area the change lives in: a module (`entra-groups`), a unit
  or component (`sentinel`, `rg-tfstate`), a workload (`exchange`), a part of
  the app (`api`). Lowercase, one word or a hyphenated term. Optional, but
  use it whenever the repository has more than one area.
- **Summary**: imperative, lowercase first letter, no full stop, at most 72
  characters for the whole first line: `fix(sentinel): read the priority of
  DSM syslog messages`.
- **`!`** and a `BREAKING CHANGE:` footer when a consumer has to change
  (a module input renamed, a reusable workflow input removed).
- **Body** explains *why*, not *what* - the diff shows what. Required unless
  the summary says everything.
- **Footer**: `Refs #12` for a related issue, `Closes #12` only in the pull
  request description (see below).
- Commits are signed (`commit.gpgsign`), with the identity verified on GitHub.
- No trace of AI anywhere: no `Co-Authored-By`, no "Generated with", no
  mention of an assistant.

**How often.** Commit every logical step that passes the local checks of the
repository (format, validate, tests). Push at least at the end of every day
and before switching to another task: an unpushed branch is a branch nobody
else can see and nothing backs up. Commits on a branch may be rough - only
the pull request title reaches `main` - but they follow the format as well.

## Pull requests

**Open a draft on the first push**, so the pipeline runs and the plan is
visible early. Mark it ready for review when it is done.

**Title** = the future commit on `main`, in the commit format above:
`feat(conditional-access): block legacy authentication`. The `pr` check
refuses a title that does not match, and sets the labels from it.

**Description** from [templates/pull_request_template.md](https://github.com/bearclave/governance/blob/main/standards/templates/pull_request_template.md)
(GitHub fills it in). The sections are not optional; write "none" rather than
delete one:

| Section | Content |
|---|---|
| Why | the problem or goal, in one to three sentences |
| What changes | bullet list of the changes a reviewer should know about |
| Impact | production yes/no, what the plan or deployment does, anything destroyed or replaced, manual steps after merge |
| Verification | what was run and seen: plan summary, tests, screenshot, run link |
| Review | summary of the `/code-review` (and `/security-review`) pass and what was left unfixed - each left-over finding becomes an issue |
| Links | `Closes #12` for the issue the PR resolves, `Refs #` for related ones |

**Size.** One logical change per pull request. The `pr` check labels the
size from the changed lines, generated files and lock files excluded:

| Label | Changed lines | Expectation |
|---|---|---|
| `size:xs` | < 10 | |
| `size:s` | < 50 | |
| `size:m` | < 200 | the normal case |
| `size:l` | < 500 | explain in Why why it cannot be split |
| `size:xl` | >= 500 | split it, unless the change is mechanical or generated |

Split along natural seams: a module change and its use in a unit are two
pull requests (module first); a rename and a behaviour change are two; a new
app feature and its refactoring are two.

**Labels.** The `pr` check sets `type:*`, `breaking` and `size:*`. Add by
hand where it applies: `prio:high`, `status:blocked`, `area:*`. Full list in
[issues.md](https://github.com/bearclave/governance/blob/main/standards/issues.md#labels).

**Assignee** is the author; the `pr` check assigns them when nobody is.
Dependabot pull requests are assigned to the owner by `dependabot.yml`.

## Review

1. The author reviews the whole diff before marking the PR ready. An AI agent
   runs `/code-review`, and `/security-review` in repositories with
   `touches-production=true`; one pass, real bugs and security findings fixed,
   the rest written into the Review section and filed as `Debt` issues
   (`source:review`).
2. All required checks green: `security / scan`, `pr / check`,
   `pipeline / gate` (names explained in [pipelines.md](https://github.com/bearclave/governance/blob/main/standards/pipelines.md)).
3. For infrastructure: the plan comment in the pull request shows only the
   intended changes. Anything destroyed or replaced is named in Impact.
4. Every review conversation resolved (the ruleset requires it).
5. There is one owner, so no approval is required; the merge itself is the
   approval. With a second person on a repository its `CODEOWNERS` adds a
   required review.

## Merge

- **Squash merge only.** The commit on `main` takes the pull request title
  and description (repository setting), so both have to be final before
  merging.
- **Branch up to date with `main`** in repositories that touch production
  (the ruleset requires it): "Update branch" re-runs the plan on the merged
  state.
- **Who merges.** Changes to live configuration (`iac-*` except
  `iac-modules`, `app-*`) are merged by the owner. An agent may merge only
  where the owner said so for that change.
- The branch is deleted by GitHub after the merge; `git uklid` and the daily
  `git-sync-main` clean up locally.
- **After the merge**, check the run on `main`: the apply or deployment went
  through, or the run is red and says why. A red run on `main` is fixed the
  same day.
- Revert with `git revert` in a new pull request titled
  `revert: <original title>`; never rewrite `main`.

## Where the rules live

| What | Where |
|---|---|
| Branch protection, merge settings, required checks, labels, issue types | [`bearclave/iac-github`](https://github.com/bearclave/iac-github) |
| PR and issue templates | [`bearclave/.github`](https://github.com/bearclave/.github) |
| Shared workflows and their checks | [`bearclave/ci`](https://github.com/bearclave/ci) |
| These rules | [`bearclave/governance`](https://github.com/bearclave/governance) `standards/` |
