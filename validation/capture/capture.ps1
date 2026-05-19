<#
.SYNOPSIS
    Golden-capture wrapper — run the Python harness for one or all corpus packages.

.DESCRIPTION
    Invokes validation/capture/capture.py via the Python virtual environment.
    Designed for Windows operators who have:
      - SQL Server Integration Services (dtexec on PATH)
      - Microsoft ODBC Driver 18 for SQL Server
      - Python 3.10+ with the validation extra installed
      - A .env file with MSSQL_* connection parameters

    See validation/capture/RUNBOOK.md for full setup instructions.

.PARAMETER PackageDir
    Path to a single corpus package directory (e.g. validation\corpus\passthrough_basic).
    Mutually exclusive with -AllPackages.

.PARAMETER AllPackages
    When specified, runs capture for every package under validation\corpus\.

.PARAMETER CorpusRoot
    Root of the corpus directory tree. Defaults to validation\corpus relative to
    the repo root.

.PARAMETER DtexecPath
    Optional path to dtexec.exe. Defaults to dtexec on PATH.

.PARAMETER VenvPython
    Path to the Python executable inside the virtual environment.
    Defaults to .venv\Scripts\python.exe (Windows venv convention).

.EXAMPLE
    # Capture golden fixtures for a single package
    .\validation\capture\capture.ps1 -PackageDir validation\corpus\passthrough_basic

.EXAMPLE
    # Capture all packages in the corpus
    .\validation\capture\capture.ps1 -AllPackages

.EXAMPLE
    # Specify a custom dtexec location
    .\validation\capture\capture.ps1 -PackageDir validation\corpus\aggregate_group `
        -DtexecPath "C:\Program Files\Microsoft SQL Server\160\DTS\Binn\dtexec.exe"
#>

[CmdletBinding(DefaultParameterSetName = 'Single')]
param(
    [Parameter(Mandatory = $true, ParameterSetName = 'Single')]
    [string] $PackageDir,

    [Parameter(Mandatory = $true, ParameterSetName = 'All')]
    [switch] $AllPackages,

    [Parameter(ParameterSetName = 'All')]
    [string] $CorpusRoot = "validation\corpus",

    [string] $DtexecPath = $null,

    [string] $VenvPython = ".venv\Scripts\python.exe"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# ---------------------------------------------------------------------------
# Locate repo root (the directory that contains the .venv folder).
# ---------------------------------------------------------------------------
$repoRoot = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $PSScriptRoot))
if (-not (Test-Path (Join-Path $repoRoot ".venv"))) {
    # Fallback: assume script is run from the repo root.
    $repoRoot = $PWD.Path
}

$python = Join-Path $repoRoot $VenvPython
if (-not (Test-Path $python)) {
    Write-Error "Python not found at $python. Run: python -m venv .venv && .venv\Scripts\pip install -e '.[validation]'"
    exit 1
}

# ---------------------------------------------------------------------------
# Build the list of packages to capture.
# ---------------------------------------------------------------------------
if ($PSCmdlet.ParameterSetName -eq 'All') {
    $corpusPath = Join-Path $repoRoot $CorpusRoot
    $packages = Get-ChildItem -Path $corpusPath -Directory | Select-Object -ExpandProperty FullName
} else {
    # Resolve relative path against repo root if not absolute.
    if ([System.IO.Path]::IsPathRooted($PackageDir)) {
        $packages = @($PackageDir)
    } else {
        $packages = @((Join-Path $repoRoot $PackageDir))
    }
}

if ($packages.Count -eq 0) {
    Write-Error "No packages found to capture."
    exit 1
}

# ---------------------------------------------------------------------------
# Run capture for each package.
# ---------------------------------------------------------------------------
$failed = @()

foreach ($pkg in $packages) {
    $pkgName = Split-Path -Leaf $pkg
    Write-Host ""
    Write-Host "=== Capturing: $pkgName ===" -ForegroundColor Cyan

    $captureArgs = @(
        "-m", "validation.capture.capture",
        "--package-dir", $pkg
    )
    if ($DtexecPath) {
        $captureArgs += "--dtexec-path", $DtexecPath
    }

    Push-Location $repoRoot
    try {
        & $python @captureArgs
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "Capture FAILED for $pkgName (exit code $LASTEXITCODE)"
            $failed += $pkgName
        } else {
            Write-Host "Capture OK: $pkgName" -ForegroundColor Green
        }
    } finally {
        Pop-Location
    }
}

# ---------------------------------------------------------------------------
# Summary.
# ---------------------------------------------------------------------------
Write-Host ""
if ($failed.Count -eq 0) {
    Write-Host "All packages captured successfully." -ForegroundColor Green
    exit 0
} else {
    Write-Warning "Failed packages: $($failed -join ', ')"
    exit 1
}
