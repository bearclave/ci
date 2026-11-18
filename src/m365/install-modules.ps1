#!/usr/bin/env pwsh
<#
    Installs the PowerShell modules every job needs, into the CurrentUser path
    the cache is keyed on.

    One list for validate, plan, apply and drift on purpose: with a smaller set
    for the validation job, whichever job ran first would populate the cache and
    the next one would skip installing and fail on a missing module.
#>
[CmdletBinding()]
param(
    # Run from the root of the calling repository; the script itself travels
    # inside the workflow (bearclave/ci).
    [string] $VersionFile = '.github/tool-versions.env'
)

$ErrorActionPreference = 'Stop'

$version = (Get-Content $VersionFile | Select-String '^M365DSC_VERSION=(.+)$').Matches[0].Groups[1].Value
Set-PSRepository -Name PSGallery -InstallationPolicy Trusted

if (-not (Get-Module -ListAvailable Microsoft365DSC | Where-Object { $_.Version.ToString() -eq $version })) {
    Install-Module -Name Microsoft365DSC -RequiredVersion $version -Force -AllowClobber -Scope CurrentUser
}
foreach ($name in @('Pester', 'PSScriptAnalyzer')) {
    if (-not (Get-Module -ListAvailable $name)) {
        Install-Module -Name $name -Force -SkipPublisherCheck -Scope CurrentUser
    }
}

& ./src/Runner/Install-M365Dependencies.ps1 -ModuleVersion $version

# A restored cache that is missing resources is worse than no cache: the plan
# gets far enough to look like a real answer and then reports whole workloads as
# "Microsoft365DSC has no resource X". The module ships around 540 resources, so
# anything far below that is a half-unpacked archive - reinstall over it once,
# and if it is still short, stop here rather than in the middle of a plan.
function Get-ResourceCount {
    param([string] $Base)
    if (-not $Base) { return 0 }
    @(Get-ChildItem -Path (Join-Path $Base 'DscResources') -Directory -ErrorAction SilentlyContinue).Count
}

function Get-Base {
    (Get-Module -ListAvailable Microsoft365DSC -Refresh |
        Where-Object { $_.Version.ToString() -eq $version } | Select-Object -First 1).ModuleBase
}

$base = Get-Base
$count = Get-ResourceCount -Base $base
if ($count -lt 500) {
    "Microsoft365DSC $version has only $count resources in '$base', installing again"
    Install-Module -Name Microsoft365DSC -RequiredVersion $version -Force -AllowClobber -Scope CurrentUser
    $base = Get-Base
    $count = Get-ResourceCount -Base $base
}
if ($count -lt 500) {
    throw "Microsoft365DSC $version in '$base' is incomplete: $count resources."
}

"Microsoft365DSC $version in $base ($count resources)"
