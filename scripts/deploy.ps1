<#
.SYNOPSIS
    Put new code live safely: pull, check it on a side port, then switch over or refuse.
.DESCRIPTION
    Use this instead of "git pull" and restarting by hand. It runs
    python -m assistant.deploy (see assistant/deploy.py):

      1. git pull (fast-forward only);
      2. each app (yours and every family copy) is started on the new code on
         a side port (its port + 100) and must pass a test conversation;
      3. passed: the apps restart on it and are checked again live (restarted
         or rolled back automatically if one fails); failed: the checkout goes
         back, the old version keeps running, and you are told.

        .\scripts\deploy.ps1                  # pull, check, switch
        .\scripts\deploy.ps1 -NoPull          # check and switch what is checked out now
        .\scripts\deploy.ps1 -Restart         # ... and restart the apps even if already on it
        .\scripts\deploy.ps1 -Requirements    # an update that changes requirements.txt
                                              # (stop the apps first)

    Never touches config, .env or data. Windows PowerShell 5.1 and 7.
#>
[CmdletBinding()]
param(
    [switch]$NoPull,
    [switch]$Restart,
    [switch]$Requirements
)
$ErrorActionPreference = 'Stop'

$repoDir = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repoDir '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    Write-Error "No .venv found in $repoDir. Create it first (README.md, Quick start)."
    exit 2
}
$arguments = @('-m', 'assistant.deploy')
if ($NoPull) { $arguments += '--no-pull' }
if ($Restart) { $arguments += '--restart' }
if ($Requirements) { $arguments += '--requirements' }
Push-Location $repoDir
try {
    & $python @arguments
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
