# Which instance types a change touches, so that a plan does not read the
# whole tenant when one policy changed. Used by select-types.ps1; the logic is
# here, apart from git and the tenant, so the tests can run it on lists.

Set-StrictMode -Version Latest

function Get-BcSelection {
    <#
        Decides from the changed paths and the instances what to plan.

        Returns Mode 'all' (the whole tenant), 'types' (only Types) or 'none'
        (nothing that a plan reads changed), with the reason.

        - src/, tenant.psd1, .github/ and any data file under config/ (a
          .psd1 every fragment can read, a profile a gap deploys) change how
          everything is evaluated: the whole tenant.
        - A fragment (config/**/*.ps1) selects the types of the instances it
          defines. A fragment that is gone selects nothing: the runner does not
          remove what is no longer described, the weekly tombstones report
          does.
        - Anything else (docs, tests, READMEs) selects nothing.
    #>
    [CmdletBinding()]
    param(
        [AllowEmptyCollection()] [string[]] $Changed,
        [AllowEmptyCollection()] [object[]] $Instances
    )

    $Changed = @($Changed | Where-Object { $_ })
    if ($Changed.Count -eq 0) {
        return [pscustomobject]@{ Mode = 'all'; Types = @(); Reason = 'no difference to the base: the whole tenant' }
    }

    $global = @($Changed | Where-Object { $_ -match '^(src/|\.github/)' -or $_ -eq 'tenant.psd1' -or ($_ -match '^config/' -and $_ -notmatch '\.(ps1|md)$') })
    if ($global.Count -gt 0) {
        return [pscustomobject]@{ Mode = 'all'; Types = @(); Reason = "$($global[0]) changes how everything is evaluated" }
    }

    $fragments = @($Changed | Where-Object { $_ -match '^config/.+\.ps1$' })
    $types = @($Instances | Where-Object { $_.Source -in $fragments } | ForEach-Object { $_.Type } | Sort-Object -Unique)
    if ($types.Count -eq 0) {
        return [pscustomobject]@{ Mode = 'none'; Types = @(); Reason = 'no fragment that describes an instance changed' }
    }
    [pscustomobject]@{ Mode = 'types'; Types = $types; Reason = "fragments changed: $($fragments -join ', ')" }
}

Export-ModuleMember -Function Get-BcSelection
