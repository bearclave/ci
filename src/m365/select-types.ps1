#!/usr/bin/env pwsh
<#
    What the plan reads: the whole tenant, only some instance types, or
    nothing. Compares HEAD with -Base (the target branch of a pull request, or
    the head of the last successful run on main) and writes mode and types to
    $GITHUB_OUTPUT. A base that is missing or not an ancestor of HEAD means
    the whole tenant.

    Needs what Get-BcContext needs (M365_APPLICATION_ID and the certificate),
    since the instances come from the same evaluation the plan uses.
#>
[CmdletBinding()]
param(
    [string] $Base
)

$ErrorActionPreference = 'Stop'
# git answers some questions by its exit code alone.
$PSNativeCommandUseErrorActionPreference = $false
Set-StrictMode -Version Latest
# Run from the root of the calling repository (the script travels inside the
# workflow, so its own location says nothing about the repository).
$repoRoot = (Get-Location).Path
Import-Module (Join-Path $PSScriptRoot 'Selection.psm1') -Force

function Write-Selection($selection) {
    $types = $selection.Types -join ','
    Write-Host "mode=$($selection.Mode) types=$types ($($selection.Reason))"
    if ($env:GITHUB_OUTPUT) {
        "mode=$($selection.Mode)" | Add-Content $env:GITHUB_OUTPUT
        "types=$types" | Add-Content $env:GITHUB_OUTPUT
    }
    if ($env:GITHUB_STEP_SUMMARY) {
        "Plan scope: **$($selection.Mode)** $(if ($types) { "($types)" }) - $($selection.Reason)." |
            Add-Content $env:GITHUB_STEP_SUMMARY
    }
}

$usable = $false
if ($Base) {
    git cat-file -e "$Base^{commit}" 2>$null
    if ($LASTEXITCODE -eq 0) {
        git merge-base --is-ancestor $Base HEAD
        $usable = $LASTEXITCODE -eq 0
    }
}
if (-not $usable) {
    Write-Selection ([pscustomobject]@{ Mode = 'all'; Types = @(); Reason = "no usable base commit ('$Base')" })
    exit 0
}

$changed = @(git diff --name-only $Base HEAD)
$instances = @()
if ($changed | Where-Object { $_ -match '^config/.+\.ps1$' }) {
    Import-Module (Join-Path $repoRoot 'src/Runner/Runner.psm1') -Force
    $context = Get-BcContext -RepoRoot $repoRoot
    $instances = @(Get-BcInstances -Context $context)
}
Write-Selection (Get-BcSelection -Changed $changed -Instances $instances)
exit 0
