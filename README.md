# ci

Shared CI for the `bearclave` organisation. The checks that are the same in
every repository live here once, as reusable workflows, instead of a copy in
each repository that drifts on its own.

## Workflows

| Workflow | What it checks | Check name in the caller |
|---|---|---|
| `security.yml` | gitleaks over the whole history, actionlint, zizmor, shellcheck | `security / scan` |

The `main-checks` ruleset of the organisation requires `security / scan` on
every repository that has workflows.

## Calling a workflow

```yaml
jobs:
  security:
    uses: bearclave/ci/.github/workflows/security.yml@v1
    permissions:
      contents: read
```

The repository is private; its Actions access is set to "Accessible from
repositories in the 'bearclave' organization", which is what lets the others
call it.

**Template repositories cannot.** `template-iac-*` are copied to customers,
whose organisations cannot reach a private `bearclave` repository, so the
templates keep their own copy of the checks.

## Versions

Callers use the moving tag `v1`, not a commit:

- A change here reaches every repository at once, when the tag moves. With a
  commit pin, every change would mean a pull request in each of a dozen
  repositories, and in the IaC repositories each of those plans the whole
  tree.
- The `tagy-verzi` ruleset lets only an organisation owner move or delete a
  `v*` tag.
- Everything the workflows themselves use is pinned to a commit; the
  organisation enforces that (`sha_pinning_required`).

A change that would break callers (a new required input, a renamed job and
with it the check name) goes to `v2`, and the callers move over one by one.

The tools (gitleaks, actionlint, zizmor, shellcheck) are release binaries
checked against a sha256, pinned in the `env` of the workflow. Dependabot does
not see them; a version and its checksum are bumped together by hand. The
checksum is the `digest` GitHub publishes with the release asset:

```bash
gh api repos/gitleaks/gitleaks/releases/tags/v8.30.1 \
  --jq '.assets[] | select(.name | test("linux_x64")) | .digest'
```

## Releasing

1. Change the workflow in a pull request. The pull request runs the changed
   workflow from its own branch (`.github/workflows/ci.yml`), so it is tried
   on this repository before anything else sees it.
2. Merge.
3. Move `v1` to the merge commit:

   ```bash
   sha=$(gh api repos/bearclave/ci/commits/main --jq .sha)
   gh api -X PATCH repos/bearclave/ci/git/refs/tags/v1 -f sha="$sha" -F force=true
   ```

4. Rerun the check in one caller to see it there too.
