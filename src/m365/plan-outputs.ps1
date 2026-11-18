#!/usr/bin/env pwsh
<#
    Turns the plan into the facts the workflow needs: whether anything would
    change, whether anything failed, and which instances differ. The apply job
    runs off the first one - an apply with nothing to do is a minute of the
    quota for nothing - and writes only the last.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $PlanPath
)

$ErrorActionPreference = 'Stop'

$plan = Get-Content -Raw -Path $PlanPath | ConvertFrom-Json
$changed = @($plan.Changed).Count
$failed = @($plan.Failed).Count
$informational = @($plan.Informational).Count

# The apply writes only these: the instances the plan found different, by
# type and name, so it does not read the whole tenant a second time.
$types = @($plan.Changed | ForEach-Object { $_.Type } | Sort-Object -Unique) -join ','
$names = @($plan.Changed | ForEach-Object { $_.Name } | Sort-Object -Unique) -join ','

if ($env:GITHUB_OUTPUT) {
    Add-Content -Path $env:GITHUB_OUTPUT -Value "has_changes=$(($changed -gt 0).ToString().ToLowerInvariant())"
    Add-Content -Path $env:GITHUB_OUTPUT -Value "has_failures=$(($failed -gt 0).ToString().ToLowerInvariant())"
    Add-Content -Path $env:GITHUB_OUTPUT -Value "changed_types=$types"
    Add-Content -Path $env:GITHUB_OUTPUT -Value "changed_names=$names"
}

"$changed to change, $failed failed, $informational report-only"
