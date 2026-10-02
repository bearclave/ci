# ci

Project documentation is in `README.md`. This file holds only the working rules.

## Working rules

These rules apply to every AI-assisted session in this repository, local or
cloud. Cloud sessions do not have the owner's local hooks and settings, so this
file and `.claude/settings.json` are the only guard there.

**Git**

- Never commit or push to `main`. Create a new branch from `main`, open one
  pull request and do not merge it unless explicitly asked.
- No force push and no history rewrite on a branch that is already pushed.
- Commit messages, PR titles and PR descriptions in Czech without diacritics:
  a short subject and a body that explains why.
- No trace of AI anywhere: no `Co-Authored-By` trailer, no "Generated with"
  footer, no mention of an assistant in commits, PR text, code or docs. If the
  git identity in the environment is an anthropic.com address, commit as
  `Matěj Hrabálek <matej.hrabalek@bearclave.cz>` instead.
- Code comments in English.

**Review before the pull request**

- Before opening the pull request, review the whole diff with `/code-review`
  and fix what it finds.
- One pass is enough: fix real bugs and security findings, list the rest in
  the PR description and do not loop on style.
- Skip the review when the change touches only docs or comments.
- If a command is not available in the session, review the diff yourself
  against the same criteria.
- End the PR description with a short review summary.

**Deploy**

Nothing is deployed from this repository. Every other repository calls its
workflows by the `v1` tag, so a merged change reaches them only when the tag
moves (see "Releasing" in `README.md`). Move it only after the pull request's
own run of the changed workflow is green.
