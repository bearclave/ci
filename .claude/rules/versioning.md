# Versioning and releases

<!-- Standard of bearclave/governance (standards/versioning.md). In other repositories this is a managed copy - change it in governance. -->

A version exists where something is **consumed by reference** or **handed
over**. Configuration that is applied from `main` has no version: `main` is
what runs, and the apply run is its record.

## Which repositories release

| Repository | Versioned | Consumers pin |
|---|---|---|
| `ci` | yes | `@v2` (major tag), production repositories the exact `@v2.3.1` |
| `iac-modules` | yes | `modules_ref` in `account.hcl` (today `main`; pinning to a tag is planned) |
| `template-iac-*` | yes | the version handed over to a customer, written into the handover |
| `app-home` | yes | the image tag the NAS runs |
| `app-office-templates` | yes | the generated templates handed to the website |
| `iac-azure`, `iac-m365`, `iac-home`, `iac-github`, `app-web-*` | no | applied or deployed from `main` |
| `governance`, `handbook`, `ops-tools`, `.github`, `content-sentinel` | no | read, not consumed |

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
  callers on `@v2` get fixes. Only the owner may move it (tag ruleset).
- No other tags. No `latest`, no date tags.

## Releasing

1. Merge everything that belongs to the release.
2. Work out the version from the titles since the last tag:
   `gh pr list --repo bearclave/<repo> --state merged --search "merged:>$(git log -1 --format=%cs <last-tag>)"`.
3. Tag and publish the release with notes generated from the pull requests:

   ```bash
   git switch main && git pull --ff-only
   git tag -s v2.5.0 -m "v2.5.0"
   git push origin v2.5.0
   gh release create v2.5.0 --verify-tag --generate-notes
   # ci only: move the major tag
   git tag -fs v2 -m "v2 -> v2.5.0" "v2.5.0^{}" && git push -f origin v2
   ```

   Moving the major tag is a force push, which `.claude/settings.json`
   denies: an agent tags and releases `vX.Y.Z`, the owner moves `vX`.

4. The release notes are grouped by the `type:*` labels of the pull requests
   (`.github/release.yml`, managed): Breaking changes, Features, Fixes,
   Performance, Dependencies, Other. Edit the generated notes only to add
   upgrade steps for a breaking change.

## Changelog

The GitHub releases are the changelog; no `CHANGELOG.md` is kept. A
repository whose consumers cannot read GitHub (templates handed to a
customer) writes the release notes into the handover instead.
