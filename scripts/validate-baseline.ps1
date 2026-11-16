[CmdletBinding()]
param(
    [string]$BaselinePath,
    [string]$HistoryPath,
    [switch]$Quiet
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repositoryRoot = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($BaselinePath)) {
    $BaselinePath = Join-Path $repositoryRoot 'data/baseline.json'
}
if ([string]::IsNullOrWhiteSpace($HistoryPath)) {
    $HistoryPath = Join-Path $repositoryRoot 'data/history.json'
}

# These constants are deliberately independent from both JSON files. Changing a
# protected baseline value therefore requires defeating this validation explicitly.
$Expected = @{
    Date                       = '2026-11-02'
    InitialCapitalAmount       = '10000000'
    InitialCapitalCurrency     = 'HUF'
    VhylIsin                   = 'IE00B8GKDB10'
    VhylExchange               = 'Euronext Amsterdam'
    VhylCurrency               = 'EUR'
    VhylInitialPrice           = '82.96'
    VhylQuantity               = '165.3498206946544387167'
    VwceIsin                   = 'IE00BK5BQT80'
    VwceExchange               = 'Xetra'
    VwceCurrency               = 'EUR'
    VwceInitialPrice           = '169.28'
    VwceQuantity               = '81.0339149623613671783'
    InitialEurHuf              = '364.50'
    InitialDividendCashAmount  = '0'
    InitialDividendCashCurrency = 'HUF'
}

function Read-JsonFile {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Description
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "$Description is missing: $Path"
    }

    try {
        return Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
    }
    catch {
        throw "$Description is not valid JSON: $Path. $($_.Exception.Message)"
    }
}

function Get-RequiredProperty {
    param(
        [Parameter(Mandatory = $true)][object]$InputObject,
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Context
    )

    $property = $InputObject.PSObject.Properties[$Name]
    if ($null -eq $property) {
        throw "$Context.$Name is missing."
    }

    return $property.Value
}

function Assert-ExactString {
    param(
        [AllowNull()][object]$Actual,
        [Parameter(Mandatory = $true)][string]$ExpectedValue,
        [Parameter(Mandatory = $true)][string]$Context
    )

    if ($Actual -isnot [string]) {
        throw "$Context must be stored as a JSON string."
    }
    if ($Actual -cne $ExpectedValue) {
        throw "$Context is protected. Expected '$ExpectedValue', found '$Actual'."
    }
}

function Assert-HistoryNumber {
    param(
        [AllowNull()][object]$Actual,
        [Parameter(Mandatory = $true)][string]$ExpectedValue,
        [Parameter(Mandatory = $true)][string]$Context
    )

    if ($null -eq $Actual -or $Actual -is [string] -or $Actual -is [bool]) {
        throw "$Context must be a JSON number."
    }

    try {
        $actualDecimal = [Convert]::ToDecimal($Actual, [Globalization.CultureInfo]::InvariantCulture)
        $expectedDecimal = [decimal]::Parse($ExpectedValue, [Globalization.CultureInfo]::InvariantCulture)
    }
    catch {
        throw "$Context must be a finite decimal number."
    }

    if ($actualDecimal -ne $expectedDecimal) {
        throw "$Context is protected. Expected $ExpectedValue, found $Actual."
    }
}

$baseline = Read-JsonFile -Path $BaselinePath -Description 'Baseline definition'

Assert-ExactString (Get-RequiredProperty $baseline 'date' 'baseline') $Expected.Date 'baseline.date'

$initialCapital = Get-RequiredProperty $baseline 'initialCapital' 'baseline'
Assert-ExactString (Get-RequiredProperty $initialCapital 'amount' 'baseline.initialCapital') $Expected.InitialCapitalAmount 'baseline.initialCapital.amount'
Assert-ExactString (Get-RequiredProperty $initialCapital 'currency' 'baseline.initialCapital') $Expected.InitialCapitalCurrency 'baseline.initialCapital.currency'

$holdings = Get-RequiredProperty $baseline 'holdings' 'baseline'
$vhyl = Get-RequiredProperty $holdings 'VHYL' 'baseline.holdings'
Assert-ExactString (Get-RequiredProperty $vhyl 'isin' 'baseline.holdings.VHYL') $Expected.VhylIsin 'baseline.holdings.VHYL.isin'
Assert-ExactString (Get-RequiredProperty $vhyl 'exchange' 'baseline.holdings.VHYL') $Expected.VhylExchange 'baseline.holdings.VHYL.exchange'
Assert-ExactString (Get-RequiredProperty $vhyl 'currency' 'baseline.holdings.VHYL') $Expected.VhylCurrency 'baseline.holdings.VHYL.currency'
Assert-ExactString (Get-RequiredProperty $vhyl 'initialPrice' 'baseline.holdings.VHYL') $Expected.VhylInitialPrice 'baseline.holdings.VHYL.initialPrice'
Assert-ExactString (Get-RequiredProperty $vhyl 'quantity' 'baseline.holdings.VHYL') $Expected.VhylQuantity 'baseline.holdings.VHYL.quantity'

$vwce = Get-RequiredProperty $holdings 'VWCE' 'baseline.holdings'
Assert-ExactString (Get-RequiredProperty $vwce 'isin' 'baseline.holdings.VWCE') $Expected.VwceIsin 'baseline.holdings.VWCE.isin'
Assert-ExactString (Get-RequiredProperty $vwce 'exchange' 'baseline.holdings.VWCE') $Expected.VwceExchange 'baseline.holdings.VWCE.exchange'
Assert-ExactString (Get-RequiredProperty $vwce 'currency' 'baseline.holdings.VWCE') $Expected.VwceCurrency 'baseline.holdings.VWCE.currency'
Assert-ExactString (Get-RequiredProperty $vwce 'initialPrice' 'baseline.holdings.VWCE') $Expected.VwceInitialPrice 'baseline.holdings.VWCE.initialPrice'
Assert-ExactString (Get-RequiredProperty $vwce 'quantity' 'baseline.holdings.VWCE') $Expected.VwceQuantity 'baseline.holdings.VWCE.quantity'

Assert-ExactString (Get-RequiredProperty $baseline 'initialEurHuf' 'baseline') $Expected.InitialEurHuf 'baseline.initialEurHuf'

$initialDividendCash = Get-RequiredProperty $baseline 'initialDividendCash' 'baseline'
Assert-ExactString (Get-RequiredProperty $initialDividendCash 'amount' 'baseline.initialDividendCash') $Expected.InitialDividendCashAmount 'baseline.initialDividendCash.amount'
Assert-ExactString (Get-RequiredProperty $initialDividendCash 'currency' 'baseline.initialDividendCash') $Expected.InitialDividendCashCurrency 'baseline.initialDividendCash.currency'

$historyDocument = Read-JsonFile -Path $HistoryPath -Description 'Portfolio history'
$history = @($historyDocument)
$baselineRecords = @(
    $history | Where-Object {
        $dateProperty = $_.PSObject.Properties['date']
        $null -ne $dateProperty -and $dateProperty.Value -is [string] -and $dateProperty.Value -ceq $Expected.Date
    }
)

if ($baselineRecords.Count -ne 1) {
    throw "Portfolio history must contain exactly one $($Expected.Date) baseline record; found $($baselineRecords.Count)."
}

$historyBaseline = $baselineRecords[0]
Assert-HistoryNumber (Get-RequiredProperty $historyBaseline 'VHYL' 'history baseline') $Expected.VhylInitialPrice 'history baseline.VHYL'
Assert-HistoryNumber (Get-RequiredProperty $historyBaseline 'VWCE' 'history baseline') $Expected.VwceInitialPrice 'history baseline.VWCE'
Assert-HistoryNumber (Get-RequiredProperty $historyBaseline 'EURHUF' 'history baseline') $Expected.InitialEurHuf 'history baseline.EURHUF'
Assert-HistoryNumber (Get-RequiredProperty $historyBaseline 'cashHUF' 'history baseline') $Expected.InitialDividendCashAmount 'history baseline.cashHUF'

if (-not $Quiet) {
    Write-Host 'Immutable portfolio baseline validation passed.'
}
