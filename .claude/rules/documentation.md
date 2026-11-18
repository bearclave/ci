---
paths:
  - "**/*.md"
---

# Documentation

<!-- Standard of bearclave/governance (standards/documentation.md). In other repositories this is a managed copy - change it in governance. -->

## What goes where

| Document | Holds | Does not hold |
|---|---|---|
| `README.md` | what the repository is, how it fits, how to change it, its pipeline | working rules for agents, history of changes |
| `CLAUDE.md` | working rules specific to this repository | documentation, organisation standards (those are in `.claude/rules/`) |
| `docs/*.md` | longer topics of the repository: setup, manual steps, decisions, out-of-scope | anything the README already says |
| `README.md` of a module | its inputs, outputs and why it is built the way it is | how a unit uses it |
| `bearclave/handbook` | know-how across repositories, `gotchas/` | documentation of one repository |
| LLM wiki | the owner's processes and where things are | technical documentation - it links here |

One fact lives in one place; everywhere else links to it.

## README structure

From [templates/README.md](https://github.com/bearclave/governance/blob/main/standards/templates/README.md). Sections in this order; a
section that does not apply is left out, not left empty.

1. **`# <repository>`** and one sentence: what it manages and for whom.
2. **How it fits** - what it depends on and what depends on it, a small
   diagram if more than two repositories are involved.
3. **Layout** - the directory tree to the depth that explains the code.
4. **How to make a change** - the commands to run locally (format, validate,
   plan, tests), and what a pull request gets checked for.
5. **Pipeline** - a table: trigger, what runs, identity. Which shared
   workflow of `bearclave/ci` it calls.
6. **Conventions** - only what is specific to this repository; the
   organisation rules are linked, not repeated.
7. **Gotchas** - links to `handbook/gotchas/` entries that bite here.
8. **Repository settings** - one fixed line:
   > Branch protection, required checks, merge settings and labels are
   > defined in [`bearclave/iac-github`](https://github.com/bearclave/iac-github)
   > (`repositories/<name>`). Engineering standards:
   > [`bearclave/governance`](https://github.com/bearclave/governance/tree/main/standards).

## Writing

- English, plain and short. Present tense, active voice: "The pipeline plans
  the units the change touches", not "Units will be planned".
- Explain **why** whenever a choice is not obvious; the code already says
  what.
- Tables for anything with more than two attributes; bullet lists over
  paragraphs; a heading every screenful.
- Code blocks with a language (` ```bash `, ` ```hcl `), commands that can be
  copied and run as they are, placeholders in `<angle brackets>`.
- Relative links inside the repository, full GitHub links to another one.
- No dates of "last updated", no author names, no changelog: git has them.
- Lines wrapped at about 80 characters in prose; tables and links may be
  longer.
- A document that describes behaviour is updated in the same pull request
  that changes the behaviour.

## Comments in code

- English. Explain why, a constraint, a trap, a link to the gotcha - never
  restate the line below.
- A workaround names what it works around and when it can go.
- No commented-out code; git keeps the old version.
- No `TODO` without an issue: `# TODO(#42): drop once the provider supports X`.
