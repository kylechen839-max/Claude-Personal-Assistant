<#
.SYNOPSIS
    Preflight on Kyle's Windows PC: every check in one pass.
.DESCRIPTION
    Prints a PASS/FAIL/SKIP table and saves a report under logs\. Secret
    values are never shown; .env is loaded by the preflight itself, into its
    own process only. Works in Windows PowerShell 5.1 and PowerShell 7.

        .\scripts\preflight.ps1
        .\scripts\preflight.ps1 --skip pytest     # any option of the Python tool
        .\scripts\preflight.ps1 --help
#>
$ErrorActionPreference = 'Stop'

$repoDir = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repoDir '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    Write-Error "No .venv found in $repoDir. Create it first (README.md, Quick start)."
    exit 2
}

Push-Location -LiteralPath $repoDir
try {
    & $python -m kwil.preflight @args
    $code = $LASTEXITCODE
}
finally {
    Pop-Location
}
exit $code
