[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repositoryRoot = Split-Path -Parent $PSScriptRoot
$validatorPath = Join-Path $PSScriptRoot 'validate-baseline.ps1'
$sourceBaselinePath = Join-Path $repositoryRoot 'data/baseline.json'
$sourceHistoryPath = Join-Path $repositoryRoot 'data/history.json'
$testRoot = Join-Path ([IO.Path]::GetTempPath()) ("etf-pulse-baseline-tests-" + [guid]::NewGuid().ToString('N'))
$script:passedTests = 0

function New-TestCase {
    param([Parameter(Mandatory = $true)][string]$Name)

    $safeName = $Name -replace '[^A-Za-z0-9_-]', '_'
    $caseRoot = Join-Path $testRoot $safeName
    $caseData = Join-Path $caseRoot 'data'
    $null = New-Item -ItemType Directory -Path $caseData -Force

    $baselinePath = Join-Path $caseData 'baseline.json'
    $historyPath = Join-Path $caseData 'history.json'
    Copy-Item -LiteralPath $sourceBaselinePath -Destination $baselinePath
    Copy-Item -LiteralPath $sourceHistoryPath -Destination $historyPath

    return [pscustomobject]@{
        Baseline = $baselinePath
        History = $historyPath
    }
}

function Write-TestJson {
    param(
        [Parameter(Mandatory = $true)][object]$Value,
        [Parameter(Mandatory = $true)][string]$Path
    )

    $Value | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $Path -Encoding UTF8
}

function Assert-ValidationPasses {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$BaselinePath,
        [Parameter(Mandatory = $true)][string]$HistoryPath
    )

    try {
        & $validatorPath -BaselinePath $BaselinePath -HistoryPath $HistoryPath -Quiet
    }
    catch {
        throw "FAILED: $Name was expected to pass. $($_.Exception.Message)"
    }

    $script:passedTests++
    Write-Host "PASS: $Name"
}

function Assert-ValidationFails {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$BaselinePath,
        [Parameter(Mandatory = $true)][string]$HistoryPath
    )

    $failedAsExpected = $false
    try {
        & $validatorPath -BaselinePath $BaselinePath -HistoryPath $HistoryPath -Quiet
    }
    catch {
        $failedAsExpected = $true
    }

    if (-not $failedAsExpected) {
        throw "FAILED: $Name was expected to fail validation."
    }

    $script:passedTests++
    Write-Host "PASS: $Name"
}

try {
    $null = New-Item -ItemType Directory -Path $testRoot

    $validCase = New-TestCase 'valid_baseline'
    Assert-ValidationPasses 'unchanged baseline and history' $validCase.Baseline $validCase.History

    $baselineMutations = @(
        @{ Name = 'baseline date'; Mutate = { param($x) $x.date = '2026-11-03' } },
        @{ Name = 'initial capital amount'; Mutate = { param($x) $x.initialCapital.amount = '9999999' } },
        @{ Name = 'initial capital currency'; Mutate = { param($x) $x.initialCapital.currency = 'EUR' } },
        @{ Name = 'VHYL ISIN'; Mutate = { param($x) $x.holdings.VHYL.isin = 'CHANGED' } },
        @{ Name = 'VHYL exchange'; Mutate = { param($x) $x.holdings.VHYL.exchange = 'Xetra' } },
        @{ Name = 'VHYL currency'; Mutate = { param($x) $x.holdings.VHYL.currency = 'USD' } },
        @{ Name = 'VHYL initial price'; Mutate = { param($x) $x.holdings.VHYL.initialPrice = '82.97' } },
        @{ Name = 'VHYL quantity'; Mutate = { param($x) $x.holdings.VHYL.quantity = '165.3498206946544387168' } },
        @{ Name = 'VWCE ISIN'; Mutate = { param($x) $x.holdings.VWCE.isin = 'CHANGED' } },
        @{ Name = 'VWCE exchange'; Mutate = { param($x) $x.holdings.VWCE.exchange = 'Euronext Amsterdam' } },
        @{ Name = 'VWCE currency'; Mutate = { param($x) $x.holdings.VWCE.currency = 'USD' } },
        @{ Name = 'VWCE initial price'; Mutate = { param($x) $x.holdings.VWCE.initialPrice = '169.29' } },
        @{ Name = 'VWCE quantity'; Mutate = { param($x) $x.holdings.VWCE.quantity = '81.0339149623613671784' } },
        @{ Name = 'initial EUR/HUF'; Mutate = { param($x) $x.initialEurHuf = '364.51' } },
        @{ Name = 'initial dividend cash amount'; Mutate = { param($x) $x.initialDividendCash.amount = '1' } },
        @{ Name = 'initial dividend cash currency'; Mutate = { param($x) $x.initialDividendCash.currency = 'EUR' } }
    )

    foreach ($mutation in $baselineMutations) {
        $case = New-TestCase ("modified_" + $mutation.Name)
        $baseline = Get-Content -LiteralPath $case.Baseline -Raw -Encoding UTF8 | ConvertFrom-Json
        & $mutation.Mutate $baseline
        Write-TestJson $baseline $case.Baseline
        Assert-ValidationFails ("modified " + $mutation.Name) $case.Baseline $case.History
    }

    $missingCase = New-TestCase 'missing_baseline_file'
    Remove-Item -LiteralPath $missingCase.Baseline
    Assert-ValidationFails 'missing baseline file' $missingCase.Baseline $missingCase.History

    $historyMutations = @(
        @{ Name = 'history baseline date'; Mutate = { param($x) $x[0].date = '2026-11-03' } },
        @{ Name = 'history VHYL price'; Mutate = { param($x) $x[0].VHYL = 82.97 } },
        @{ Name = 'history VWCE price'; Mutate = { param($x) $x[0].VWCE = 169.29 } },
        @{ Name = 'history EUR/HUF'; Mutate = { param($x) $x[0].EURHUF = 364.51 } },
        @{ Name = 'history dividend cash'; Mutate = { param($x) $x[0].cashHUF = 1 } }
    )

    foreach ($mutation in $historyMutations) {
        $case = New-TestCase ("modified_" + $mutation.Name)
        $historyDocument = Get-Content -LiteralPath $case.History -Raw -Encoding UTF8 | ConvertFrom-Json
        $history = @($historyDocument)
        & $mutation.Mutate $history
        Write-TestJson $history $case.History
        Assert-ValidationFails ("modified " + $mutation.Name) $case.Baseline $case.History
    }

    Write-Host "All $script:passedTests baseline protection tests passed."
}
finally {
    if (Test-Path -LiteralPath $testRoot) {
        $resolvedTestRoot = [IO.Path]::GetFullPath($testRoot)
        $resolvedTempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
        if (-not $resolvedTestRoot.StartsWith($resolvedTempRoot, [StringComparison]::OrdinalIgnoreCase) -or
            -not ([IO.Path]::GetFileName($resolvedTestRoot)).StartsWith('etf-pulse-baseline-tests-', [StringComparison]::Ordinal)) {
            throw "Refusing to clean unexpected test directory: $resolvedTestRoot"
        }
        [IO.Directory]::Delete($resolvedTestRoot, $true)
    }
}
