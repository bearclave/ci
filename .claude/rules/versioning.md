# Versioning and releases

<!-- Standard of bearclave/governance (standards/versioning.md). In other repositories this is a managed copy - change it in governance. -->

A version exists where something is **consumed by reference** or **handed
over**. Configuration that is applied from `main` has no version: `main` is
what runs, and the apply run is its record.

## Which repositories release

| Repository | Versioned | Consumers pin |
|---|---|---|
| `ci` | yes | `@v2` (major tag), production repositories the exact `@v2.3.1` |
| `iac-modules` | yes | `modules_ref` in `account.hcl`: today `main`, a tag after the first release ([below](#first-releases)) |
| `template-iac-*` | yes | the version handed over to a customer, written into the handover |
| `app-home` | yes | the image tag the NAS runs (today `:main`) |
| `app-office-templates` | yes | the generated templates handed to the website |
| `vs-code-extensions` | per extension | the `version` in each extension's `package.json`, built into its VSIX; no git tag ([below](#vs-code-extensions)) |
| `iac-*` but `iac-modules`, `app-web-*` | no | applied or deployed from `main` |
| `governance`, `handbook`, `ops-tools`, `.github`, `content-sentinel` | no | read, not consumed |

## First releases

`iac-modules`, `template-iac-*`, `app-home` and `app-office-templates` are
versioned by this standard but have no tag yet. They start releasing, in
this order:

1. **`iac-modules` `v1.0.0`** from the current `main`: the modules are in
   production use, so the first version is not a `0.x`.
2. **Pin the consumers** in their own pull requests: `modules_ref` in the
   `account.hcl` of `iac-azure` and `iac-github` from `main` to `v1.0.0`. The
   plan of each shows no change; from then on a module change reaches
   production only through a bump pull request in the consumer, with its
   plan. Dependabot does not read `modules_ref`, so the bump is made by hand
   after each release.
3. **The others `v1.0.0`** with their next change a consumer receives: a
   template at its next handover, `app-office-templates` when it next hands
   templates to the website, `app-home` with its next image (`container.yml`
   pushes `:main` and `:<sha>` today; an image tagged with the version needs
   a change there first).

Until a repository has its first tag, its consumers use `main` as today.
The version is worked out the same way for the first release as for every
later one ([Releasing](#releasing)); with no earlier tag it is `v1.0.0`.

## When to release

An agent applies this rule after every merge it was part of, and the owner
before a consumer needs a change:

| Repository | Release when | Who |
|---|---|---|
| `ci` | a merged pull request since the last tag has a release effect (below) and a caller needs it - at the latest at the end of the week of the merge | the owner tags; `major-tag.yml` and `ci-pins.yml` follow |
| `iac-modules` | a consumer is to receive a module change: release first, then bump `modules_ref` in the consumer | the owner |
| `app-home` | the change is to run on the NAS | the owner |
| `app-office-templates` | the templates are handed to the website | the owner |
| `template-iac-*` | before a handover to a customer | the owner |
| `vs-code-extensions` | in the pull request that changes an extension: its `package.json` version is bumped there | the author |

- No release for `docs`, `test`, `ci` and `chore` alone.
- An agent never tags or publishes a release (the settings deny `git tag`,
  `gh release create`): it says in its reply that a release is due, with the
  version worked out below and the commands of [Releasing](#releasing).

## Version numbers

[Semantic Versioning 2.0](https://semver.org/): `vMAJOR.MINOR.PATCH`, always
with the `v`.

The next version follows from the pull request titles merged since the last
tag (see [workflow.md](https://github.com/bearclave/governance/blob/main/standards/workflow.md#commits)):

- any `!` / `BREAKING CHANGE` → **major** (`v2.4.1` → `v3.0.0`)
- otherwise any `feat` → **minor** (`v2.4.1` → `v2.5.0`)
- otherwise `fix`, `perf`, `refactor`, `deps`, `revert` → **patch** (`v2.4.1` → `v2.4.2`)
- only `docs`, `test`, `ci`, `chore` → no release needed

Before `v1.0.0` (a new repository), breaking changes bump the minor.

## Tags

- **Release tag** `vX.Y.Z`: annotated, signed (`tag.gpgsign`), on a commit of
  `main`, never moved or deleted. The tag ruleset in `iac-github` refuses an
  update or deletion.
- **Major tag** `vX` (only in `ci`): moves to the newest `vX.Y.Z`, so that
  callers on `@v2` get fixes. Only the owner and the GitHub App
  `bearclave-bot` may move it (tag ruleset); `vX` is a lightweight ref to
  the release's commit.
- No other tags. No `latest`, no date tags.

## Releasing

A release is made by the owner, by hand, with a tag signed by his key; a
workflow cannot sign as him, and the ruleset `version-tags-creation` lets
only organisation owners create `v*` tags.

1. Merge everything that belongs to the release.
2. Work out the version and preview the notes: the workflow `release.yml` of
   `bearclave/ci` in its default dry run (where a repository has a caller), or
   by hand from the titles since the last tag:
   `gh pr list --repo bearclave/<repo> --state merged --search "merged:>$(git log -1 --format=%cs <last-tag>)"`.
   Publishing through `release.yml` is not used: it would create an
   unsigned tag, and no `RELEASE_TOKEN` secret exists.
3. Tag and publish the release with notes generated from the pull requests:

   ```bash
   git switch main && git pull --ff-only
   git tag -s v2.5.0 -m "v2.5.0"
   git push origin v2.5.0
   gh release create v2.5.0 --verify-tag --generate-notes
   ```

   In `ci` the major tag then moves by itself: publishing the release runs
   `major-tag.yml`, which moves `v2` as `bearclave-bot` to a published,
   non-pre-release release with a verified signed tag, only forward. Check
   it with `gh run list --repo bearclave/ci --workflow major-tag.yml --limit 1`.
   The manual fallback, and the first tag of a new major (`v3`), is
   `tools/move-major-tag.sh v2.5.0` in `ci`, run by the owner.

4. The release notes are grouped by the `type:*` labels of the pull requests
   (`.github/release.yml`, managed): Breaking changes, Features, Fixes,
   Performance, Dependencies, Other. Edit the generated notes only to add
   upgrade steps for a breaking change.

## vs-code-extensions

Each extension is versioned on its own, SemVer in its `package.json`, by the
same rules as the titles above; the pull request that changes an extension
bumps its version, and its README table follows. The VSIX is built locally
(`tools/build.py`) and installed by that version; nothing is published to the
Marketplace and no git tag is made, because one tag cannot carry six
versions.

## Changelog

The GitHub releases are the changelog; no `CHANGELOG.md` is kept. A
repository whose consumers cannot read GitHub (templates handed to a
customer) writes the release notes into the handover instead.
