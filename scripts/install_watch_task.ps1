<#
.SYNOPSIS
    Check every Kwil web app on this PC every 5 minutes, automatically.
.DESCRIPTION
    Registers the Task Scheduler task \Kwil\Watch: every 5 minutes (and
    at sign-in), hidden, it runs

        pythonw -m kwil.watch

    which checks your app and each family copy (config.<name>.toml), runs a
    test conversation after every restart or new version and every
    [watch] functional_hours, restarts an app that is down once, rolls back
    to the last version that passed if that doesn't help, and tells you (a
    banner in your phone app, the dev team coordinator) when something
    breaks or recovers. Results: logs\watch.log and data\watch_status.json;
    `python -m kwil.watch --status` prints them.

        .\scripts\install_watch_task.ps1              # install and run once now
        .\scripts\install_watch_task.ps1 -Uninstall   # remove the task

    The same task from before the Kwil rebrand (\HeyClaude\Watch) is
    replaced (and removed by -Uninstall).

    No administrator rights needed. Windows PowerShell 5.1 and 7.
#>
[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSAvoidUsingWriteHost', '',
    Justification = 'Interactive installer: output is for the person running it.')]
[CmdletBinding()]
param(
    [switch]$Uninstall
)
$ErrorActionPreference = 'Stop'

$repoDir = Split-Path -Parent $PSScriptRoot
$taskPath = '\Kwil\'
$oldTaskPath = '\HeyClaude\'  # the folder before the Kwil rebrand
$taskName = 'Watch'
$pythonw = Join-Path $repoDir '.venv\Scripts\pythonw.exe'
$user = "$env:USERDOMAIN\$env:USERNAME"

function Get-WatchArgument {
    '-m kwil.watch'
}

function Unregister-WatchTask {
    param([string[]]$Paths)
    foreach ($path in $Paths) {
        if (Get-ScheduledTask -TaskPath $path -TaskName $taskName -ErrorAction SilentlyContinue) {
            Stop-ScheduledTask -TaskPath $path -TaskName $taskName -ErrorAction SilentlyContinue
            Unregister-ScheduledTask -TaskPath $path -TaskName $taskName -Confirm:$false
            Write-Host "Removed task $path$taskName"
        }
    }
}

if ($Uninstall) {
    Unregister-WatchTask -Paths @($taskPath, $oldTaskPath)
    exit 0
}
if (-not (Test-Path -LiteralPath $pythonw)) {
    Write-Error "No .venv found in $repoDir. Create it first (README.md, Quick start)."
    exit 2
}
Unregister-WatchTask -Paths @($oldTaskPath)  # two watchdogs would double every alert
$action = New-ScheduledTaskAction -Execute $pythonw -Argument (Get-WatchArgument) -WorkingDirectory $repoDir
$every = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5)
$logon = New-ScheduledTaskTrigger -AtLogOn -User $user
$logon.Delay = 'PT2M'  # after the apps themselves are up
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -MultipleInstances IgnoreNew -Hidden `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2)
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskPath $taskPath -TaskName $taskName -Action $action `
    -Trigger @($every, $logon) -Settings $settings -Principal $principal -Force `
    -Description 'Kwil watchdog: checks every web app here (kwil/watch.py)' | Out-Null
Start-ScheduledTask -TaskPath $taskPath -TaskName $taskName
Write-Host "Installed $taskPath$taskName (every 5 minutes, hidden) and ran it once."
Write-Host 'Results: python -m kwil.watch --status (or logs\watch.log).'
