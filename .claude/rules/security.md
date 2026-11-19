# Security

<!-- Standard of bearclave/governance (standards/security.md). In other repositories this is a managed copy - change it in governance. -->

## Secrets

- **No secret in a repository**, ever - not in code, not in a test fixture,
  not in a commented-out line, not in history. gitleaks scans the whole
  history on every pull request (`security / scan`); the push ruleset
  `push-restricted-files` refuses `*.tfstate*`, `.terraform/`, `*.tfvars`, `*.tfvars.json`, `*.pfx`, `*.p12`, `*.pem`, `*.key`, `*.p8`, `*.ppk`, `*.jks`, `*.keystore`, `id_rsa`, `id_ecdsa`, `id_ed25519`, `*.kdbx`, `*.ovpn`, `.env*` and files over 5 MB.
  `*.mobileconfig` is allowed: the Intune profiles of `iac-m365` are
  configuration, not secrets.
- **No secret in GitHub either**, where avoidable. Pipelines sign in to Azure
  by workload identity federation (OIDC) and read what they need from a Key
  Vault (GitHub App keys included). The only GitHub secret is
  `MODULES_DEPLOY_KEY` in the Terragrunt repositories: the private half of a
  read-only deploy key of `iac-modules`, one key per consuming repository,
  declared in `iac-github` (`deploy_keys` of `repositories/iac-modules`).
  Rotation: once a year or at once on suspicion, by the procedure in
  `iac-github` `docs/deploy-keys.md` - a new key pair, the public key in code, the secret
  set by the owner, the old key removed by the next merge.
- Identifiers that are not secret (tenant, client and subscription IDs) are
  repository variables, never hard-coded in a workflow.
- A false positive of gitleaks goes into `.gitleaks.toml` with the reason
  and a review date ([pipelines.md](https://github.com/bearclave/governance/blob/main/standards/pipelines.md#checks-and-their-exceptions));
  the step is never switched off.
- A secret that reached a commit is **rotated**, not only removed: history
  is public to everyone with read access.

## Identities

- One identity per pipeline and purpose, least privilege
  ([identities.md](https://github.com/bearclave/governance/blob/main/standards/identities.md)). A pull request and the drift plan as a
  read-only identity federated on `pull_request` and `main`; apply runs as
  the write identity federated **only** on the production environment.
- Federated credentials use the immutable subject with the organisation and
  repository IDs, so a credential cannot pass to a re-created repository.
- Every identity is declared in `iac-azure` (`tenant/applications`,
  `tenant/federated-credentials`) with a description of what it may do, and
  listed in `bearclave/ci` `docs/identities.md`.
- GitHub Apps get the permissions their purpose needs, set in code
  (`iac-github` `tools/`); an App installed on all repositories with write
  access (such as `claude`) is limited to the repositories it works in.

## Supply chain

- Actions are pinned to a full commit SHA with the version in a comment;
  the organisation allows only GitHub-owned (`actions/*`, `github/*`),
  `azure/*`, `hashicorp/*` and `bearclave/*` actions - not "verified
  creators" (`verified_allowed = false`); everything else is a pinned
  binary.
- Other tools are downloaded as release binaries and checked against a
  sha256 from the repository's `.github/tool-versions.env`, or from
  `security.yml` of `bearclave/ci` for the security tools
  ([toolchain.md](https://github.com/bearclave/governance/blob/main/standards/toolchain.md)).
- Updates come as pull requests the owner approves and merges, only for
  releases at least 7 days old; who opens which is in
  [toolchain.md](https://github.com/bearclave/governance/blob/main/standards/toolchain.md#dependency-updates). Dependabot security
  updates are on in every repository and come at once, whatever the
  schedule.
- Terraform providers and Microsoft365DSC are pinned to exact versions and
  providers verified by the committed `.terraform.lock.hcl`; a bump is a
  `deps` pull request with its plan ([toolchain.md](https://github.com/bearclave/governance/blob/main/standards/toolchain.md)).

## Workflows

- `permissions: contents: read` at the top, more only on the job that needs
  it.
- `persist-credentials: false` on every checkout unless the job pushes.
- No `${{ }}` of untrusted input inside `run:`; pass it through `env:`.
  zizmor and actionlint check this on every pull request.
- No `pull_request_target`, no self-hosted runners (the organisation allows
  none), `runs-on: ubuntu-24.04`.
- A job that signs in with a write identity runs in its environment
  (deployment branches `main` only); nothing a pull request runs can reach
  it.
- Production callers (`touches-production=true`) pin `bearclave/ci` to an
  exact release, never a moving major tag.

## Reviews and what GitHub enforces

- Every pull request: one approval of the final diff, review threads
  resolved, the three required checks, signed commits (rulesets in
  `iac-github`); `CODEOWNERS` (`@bearclave/owners`) requests the review.
- An agent runs `/code-review`, and `/security-review` where
  `touches-production=true` ([workflow.md](https://github.com/bearclave/governance/blob/main/standards/workflow.md#review)).
- GitHub Team has no secret scanning, push protection or code scanning for
  private repositories (paid add-ons, not bought): gitleaks on every pull
  request and the push ruleset stand in for them.

## Reporting

A weakness found in code or in a managed tenant is a `Security` issue
([issues.md](https://github.com/bearclave/governance/blob/main/standards/issues.md)); one with real exposure is `prio:high` and is
fixed before other work.
