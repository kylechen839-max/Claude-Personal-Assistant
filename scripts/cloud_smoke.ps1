<#
.SYNOPSIS
    Smoke-check a deployed web app (the Render service, or the Pi).
.DESCRIPTION
    Checks /healthz, that a protected route refuses a request without a
    session, logs in with $env:WEB_TOKEN (or WEB_TOKEN from .env; never
    printed), then runs one text turn and one audio turn. Exit code 0 means
    every check passed. Works in Windows PowerShell 5.1 and PowerShell 7.

        .\scripts\cloud_smoke.ps1 https://hey-claude.onrender.com
#>
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$BaseUrl,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Rest
)
$ErrorActionPreference = 'Stop'

$repoDir = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repoDir '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    $python = 'python'
}

Push-Location -LiteralPath $repoDir
try {
    if ($Rest) {
        & $python -m kwil.smoke $BaseUrl @Rest
    }
    else {
        & $python -m kwil.smoke $BaseUrl
    }
    $code = $LASTEXITCODE
}
finally {
    Pop-Location
}
exit $code
