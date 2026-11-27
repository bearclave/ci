# Releasing

How `ci` is versioned and released. The rules for every repository are in
[versioning.md](https://github.com/bearclave/governance/blob/main/standards/versioning.md);
this page holds what is specific to `ci`.

## Versions

- Every release is a signed tag `v2.Y.Z` and a GitHub release; the major
  tag `v2` moves to the newest.
- Callers use `@v2`; repositories that touch production pin `@v2.Y.Z` and
  get the bump as a pull request (`ci-pins.yml`).
- A change that would break callers (a new required input, a renamed job
  and with it the check name) is a major version.
- `v1` stays where it is for anything that still calls it; it gets no
  changes.

## Pins

Everything the workflows use is pinned to a commit (`sha_pinning_required`).
The downloaded tools are release binaries checked against a sha256:

| Tools | Pinned in |
|---|---|
| gitleaks, actionlint, zizmor, shellcheck | the `env` of `templates/security.yml` |
| markdownlint-cli2 | the image digest in `templates/pr.yml` |
| Terraform, Terragrunt, TFLint, Trivy, ... | the caller's `.github/tool-versions.env` |

The sha256 of a release asset:

```bash
gh api repos/gitleaks/gitleaks/releases/tags/v8.30.1 \
  --jq '.assets[] | select(.name | test("linux_x64")) | .digest'
```

## Cadence

Every release fans out a `ci-pins` pull request to each repository that pins
`v2.Y.Z`, and in a Terragrunt repository each of those plans the whole
tree, which costs Actions minutes. So:

- **At most one release a week**, on a fixed day (Monday), collecting what
  merged since. Merge as often as needed; releasing is the owner's choice and
  a merge alone changes nothing for a pinned caller.
- **Out of cadence only for** a fix to a broken pipeline, a security fix, or
  something a repository is blocked on. Say which in the release notes.
- `ci-pins.py` does not bump a repository when none of the workflows it calls
  changed between its pin and the release (an `iac-*` repository is left
  alone by a release that only touches `container.yml`), and refuses a tag
  that is lightweight or not verified signed. A skipped repository is listed
  in the summary of the run; its pin catches up with the next release that
  does change a workflow it calls.

## Making a release

`ci` is released by the owner, with `tools/release.sh`, the only way to
release ([versioning.md](https://github.com/bearclave/governance/blob/main/standards/versioning.md#releasing)):
its tags are signed, which a workflow cannot do. `release.yml` works out the
same version and notes in its dry run. An agent only says that a release is due.

1. Change the workflow in a pull request; merge it once its own run is
   green.
2. From a clone of `ci`, at a terminal:

   ```bash
   tools/release.sh <repo-path> --dry-run   # the checks and the version
   tools/release.sh <repo-path>             # type the version to confirm
   ```

   The script is described in the [README](../README.md).
3. Publishing runs `major-tag.yml` (moves `v2` as `bearclave-bot`) and
   `ci-pins.yml` (a pull request in every repository that pins an older
   `v2.Y.Z`). Check them with
   `gh run list --workflow major-tag.yml --limit 1`.
4. The next pull request of a caller on `@v2` runs the new release; the
   pinned callers run it in their `ci-pins` pull request.

**Fallback.** When `major-tag.yml` fails, and for the first tag of a new
major, the owner runs `tools/move-major-tag.sh v2.5.0`. It force-pushes the
major tag, so an agent never runs it.
