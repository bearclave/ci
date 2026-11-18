---
paths:
  - "**/*.ps1"
  - "**/*.psm1"
  - "**/*.psd1"
---

# PowerShell

<!-- Standard of bearclave/governance (standards/code/powershell.md). In other repositories this is a managed copy - change it in governance. -->

- PowerShell 7 (`pwsh`), runs on Linux and macOS; no Windows-only cmdlets.
- `[CmdletBinding()]`, a `param()` block with types, comment-based help
  (`.SYNOPSIS`, `.DESCRIPTION`, `.EXAMPLE`) on every script and exported
  function.
- `$ErrorActionPreference = 'Stop'` at the top of every script.
- Approved verbs (`Get-Verb`), `Verb-Noun` in PascalCase, singular nouns.
- Data in `.psd1` files (`Import-PowerShellDataFile`), never in code;
  configuration fragments validated against their schema
  (`tools/Test-Configuration.ps1` in `iac-m365`).
- No `Write-Host` for data; output objects, `Write-Verbose` for tracing.
- Secrets never as parameters or in variables printed to the log;
  certificates come from Key Vault at run time.
- PSScriptAnalyzer with the repository's `.pssa.psd1` must be clean of
  errors; Pester tests (`tests/*.Tests.ps1`) for logic that decides what is
  applied.
- Pinned module versions (`.github/tool-versions.env`), `-RequiredVersion`
  on `Install-Module`.
