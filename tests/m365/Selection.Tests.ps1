# What a change selects for the plan of iac-m365 (src/m365/Selection.psm1).

BeforeAll {
    Import-Module (Join-Path $PSScriptRoot '../../src/m365/Selection.psm1') -Force
    $script:instances = @(
        [pscustomobject]@{ Type = 'AADConditionalAccessPolicy'; Name = 'CA01'; Source = 'config/entra/access/AADConditionalAccessPolicy.ps1' }
        [pscustomobject]@{ Type = 'AADConditionalAccessPolicy'; Name = 'CA02'; Source = 'config/entra/access/AADConditionalAccessPolicy.ps1' }
        [pscustomobject]@{ Type = 'AADNamedLocationPolicy'; Name = 'zeme'; Source = 'config/entra/access/AADNamedLocationPolicy.ps1' }
        [pscustomobject]@{ Type = 'EXOTransportRule'; Name = 'r'; Source = 'config/exchange/mail/EXOTransportRule.ps1' }
        [pscustomobject]@{ Type = 'IntuneDefenderConnector'; Name = 'g'; Source = 'src/Gaps/IntuneDefenderConnector.psm1' }
    )
}

Describe 'Get-BcSelection' {
    It 'selects the types a changed fragment defines' {
        $s = Get-BcSelection -Changed @('config/entra/access/AADNamedLocationPolicy.ps1') -Instances $instances
        $s.Mode | Should -Be 'types'
        $s.Types | Should -Be @('AADNamedLocationPolicy')
    }

    It 'names a type once however many instances its fragment holds' {
        $s = Get-BcSelection -Changed @('config/entra/access/AADConditionalAccessPolicy.ps1') -Instances $instances
        $s.Types | Should -Be @('AADConditionalAccessPolicy')
    }

    It 'adds up several fragments' {
        $s = Get-BcSelection -Changed @('config/entra/access/AADNamedLocationPolicy.ps1',
            'config/exchange/mail/EXOTransportRule.ps1', 'README.md') -Instances $instances
        $s.Types | Should -Be @('AADNamedLocationPolicy', 'EXOTransportRule')
    }

    It 'plans everything for <path>' -ForEach @(
        @{ path = 'src/Runner/Runner.psm1' }
        @{ path = 'src/Gaps/IntuneDefenderConnector.psm1' }
        @{ path = 'tenant.psd1' }
        @{ path = '.github/workflows/m365.yml' }
        @{ path = 'config/admin/licensing/CommerceSelfServicePurchase.psd1' }
        @{ path = 'config/intune/macos/profiles/sso-platform.mobileconfig' }
    ) {
        (Get-BcSelection -Changed @($path, 'config/exchange/mail/EXOTransportRule.ps1') -Instances $instances).Mode |
            Should -Be 'all'
    }

    It 'plans nothing for docs and tests alone' {
        $s = Get-BcSelection -Changed @('README.md', 'docs/manual-steps.md', 'tests/Gaps.Tests.ps1', 'config/README.md') -Instances $instances
        $s.Mode | Should -Be 'none'
    }

    It 'plans nothing for a fragment that is gone' {
        (Get-BcSelection -Changed @('config/exchange/mail/Removed.ps1') -Instances $instances).Mode | Should -Be 'none'
    }

    It 'plans everything when there is no difference at all' {
        (Get-BcSelection -Changed @() -Instances $instances).Mode | Should -Be 'all'
    }
}
