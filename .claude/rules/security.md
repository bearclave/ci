# Security

<!-- Standard of bearclave/governance (standards/security.md). In other repositories this is a managed copy - change it in governance. -->

## Secrets

- **No secret in a repository**, ever - not in code, not in a test fixture,
  not in a commented-out line, not in history. gitleaks scans the whole
  history on every pull request (`security / scan`); the push ruleset refuses
  `*.tfstate*`, `.terraform/`, `*.pfx`, `*.p12`, `*.pem`, `*.key`, `.env*`
  and files over 5 MB.
- **No secret in GitHub either**, where avoidable. Pipelines sign in to Azure
  by workload identity federation (OIDC) and read what they need from a Key
  Vault. The only GitHub secret is `MODULES_DEPLOY_KEY` (read-only deploy key
  of `iac-modules`).
- Identifiers that are not secret (tenant, client and subscription IDs) are
  repository variables, never hard-coded in a workflow.
- A false positive of gitleaks goes into `.gitleaks.toml` with the reason
  and a review date ([pipelines.md](https://github.com/bearclave/governance/blob/main/standards/pipelines.md#checks-and-their-exceptions));
  the step is never switched off.
- A secret that reached a commit is **rotated**, not only removed: history
  is public to everyone with read access.

## Identities

- One identity per pipeline and purpose, least privilege. A pull request
  plans as a read-only identity federated on `pull_request` only; apply runs
  as the write identity federated on the production environment and `main`.
- Federated credentials use the immutable subject with the organisation and
  repository IDs, so a credential cannot pass to a re-created repository.
- Every identity is declared in `iac-azure` (`tenant/applications`,
  `tenant/federated-credentials`) with a description of what it may do.

## Supply chain

- Actions are pinned to a full commit SHA with the version in a comment;
  the organisation allows only GitHub-owned, verified, `azure/*` and
  `hashicorp/*` actions.
- Other tools are downloaded as release binaries and checked against a
  sha256 from `tool-versions.env` of `bearclave/ci` ([toolchain.md](https://github.com/bearclave/governance/blob/main/standards/toolchain.md)).
- Dependabot opens one grouped pull request a week, only for releases at
  least 7 days old.
- Terraform providers and Microsoft365DSC are pinned to exact versions and
  providers verified by the committed `.terraform.lock.hcl`; a bump is a
  `deps` pull request with its plan ([toolchain.md](https://github.com/bearclave/governance/blob/main/standards/toolchain.md)).

## Workflows

- `permissions: contents: read` at the top, more only on the job that needs
  it.
- `persist-credentials: false` on every checkout unless the job pushes.
- No `${{ }}` of untrusted input inside `run:`; pass it through `env:`.
  zizmor and actionlint check this on every pull request.
- No `pull_request_target`, no self-hosted runners.

## Reporting

A weakness found in code or in a managed tenant is a `Security` issue
([issues.md](https://github.com/bearclave/governance/blob/main/standards/issues.md)); one with real exposure is `prio:high` and is
fixed before other work.
