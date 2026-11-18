# AI coding agents

<!-- Standard of bearclave/governance (standards/agents.md). In other repositories this is a managed copy - change it in governance. -->

How an AI coding agent (Claude Code, locally or in a cloud session) works in
a `bearclave` repository. A cloud session has none of the owner's local
hooks or settings: this file, the other files in `.claude/rules/`, the
repository's `CLAUDE.md` and `.claude/settings.json` are the only guard
there, so they have to be enough on their own.

## Where the instructions are

| File | Content | Edited in |
|---|---|---|
| `CLAUDE.md` | what is specific to this repository: what it deploys, what not to touch, its commands | the repository |
| `.claude/rules/*.md` | the organisation standards (this file and its siblings) | `bearclave/governance`, copied by `sync.py` |
| `.claude/settings.json` | denied commands (push to `main`, force push, apply, destroy), no attribution | `bearclave/governance`, copied by `sync.py` |
| `README.md` | documentation of the repository | the repository |

`CLAUDE.md` holds only working rules; documentation goes to the README. The
rules in `.claude/rules/` win over habits and defaults of the tool.

## Working rules

**Git and GitHub**

- Never commit or push to `main`. Start every task with a new branch from a
  fresh `origin/main` (`git novy <type>/<slug>`, see
  [workflow.md](https://github.com/bearclave/governance/blob/main/standards/workflow.md#branches)), unless told to continue on an existing
  branch.
- No force push and no history rewrite of a pushed branch.
- Commits, the pull request title and description in the formats of
  [workflow.md](https://github.com/bearclave/governance/blob/main/standards/workflow.md), in English.
- No trace of AI anywhere: no `Co-Authored-By` trailer, no "Generated with"
  footer, no mention of an assistant in commits, pull requests, issues, code
  or docs. If the git identity in the environment is an anthropic.com
  address, commit as `Matěj Hrabálek <matej.hrabalek@bearclave.cz>`.
- Open the pull request as a draft, assigned to the owner. Do not merge it
  unless the owner said so for this change.

**Before the pull request is ready**

- Run the repository's local checks (format, validate, tests, a plan where
  the repository has one - never apply).
- Review the whole diff with `/code-review` and fix what it finds. In a
  repository with `touches-production=true` (live infrastructure or a public
  application) run `/security-review` as well.
- One pass: fix real bugs and security findings, do not loop on style. Every
  finding left unfixed goes into the Review section of the description and
  becomes a `Debt` or `Security` issue ([issues.md](https://github.com/bearclave/governance/blob/main/standards/issues.md#when-an-agent-creates-an-issue)).
- Skip the review when the change touches only docs or comments.

**Infrastructure**

- Locally only `fmt`, `validate` and `plan`. Apply belongs to the pipeline on
  `main`.
- Nothing is configured by hand in a portal. Instead of instructions to click,
  propose the change in code. A change already made by hand becomes a
  `Debt` issue with `source:manual-change` saying exactly what changed.
- Read the plan comment in the pull request; it must show only the intended
  changes. Name anything destroyed or replaced in the Impact section.

**Issues**

- Create issues for what is found and not fixed, as
  [issues.md](https://github.com/bearclave/governance/blob/main/standards/issues.md#when-an-agent-creates-an-issue) says, without asking.
- Never close an issue the current pull request does not resolve.

**Questions**

- Ask only when a fact cannot be found in the repository, its README, these
  rules or the code, or before an action that cannot be undone. One question
  at a time.
