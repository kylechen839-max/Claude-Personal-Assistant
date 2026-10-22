<#
.SYNOPSIS
    Restart one Hey Claude web app through its Task Scheduler task.
.DESCRIPTION
    Used by the watchdog (python -m assistant.watch) to heal an app that is
    down, and by python -m assistant.deploy to switch an app to new code:

        .\scripts\restart_instance.ps1 -Name main   # \HeyClaude\Web (your own app)
        .\scripts\restart_instance.ps1 -Name mom    # \HeyClaude\Family-mom

    Stops the task, stops any copy of that app still running (a hung one, or
    one started by hand, so the new one gets the port), then starts the task.
    Never touches the go-live check's side-port copies (--candidate).
    Your own app may still run from an older hand-made task, "\Hey Claude Web"
    (install_windows_service.ps1 offers to replace it); without \HeyClaude\Web
    that task is restarted instead, so a deploy doesn't leave it on old code.
    Exit codes: 0 restarted, 3 the task isn't installed. No administrator
    rights needed. Windows PowerShell 5.1 and 7.
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9_-]+$')]
    [string]$Name
)
$ErrorActionPreference = 'Stop'

$taskPath = '\HeyClaude\'

function Get-TaskName {
    param([string]$Instance)
    if ($Instance -eq 'main') { return 'Web' }
    return "Family-$Instance"
}

function Get-TaskLocation {
    # Where the instance's task may live, in order: ours first, then (for
    # your own app only) the older hand-made "\Hey Claude Web" task.
    param([string]$Instance)
    $list = @(@{ Path = '\HeyClaude\'; Name = (Get-TaskName $Instance) })
    if ($Instance -eq 'main') { $list += @{ Path = '\'; Name = 'Hey Claude Web' } }
    return $list
}

function Test-InstanceCommandLine {
    # Is this a running copy of that instance's web app (not a side-port candidate)?
    param([string]$Instance, [string]$CommandLine)
    if (-not $CommandLine -or $CommandLine -match '--candidate') { return $false }
    if ($Instance -eq 'main') {
        $web = ($CommandLine -match '-m\s+assistant\.host\s+web\b') -or
            ($CommandLine -match '-m\s+assistant\b.*--mode\s+web\b')
        return $web -and ($CommandLine -notmatch '--config\s+"?config\.[A-Za-z0-9_-]+\.toml')
    }
    return $CommandLine -match ('-m\s+assistant\b.*--config\s+"?' + [regex]::Escape("config.$Instance.toml") + '(\s|"|$)')
}

$found = Get-TaskLocation $Name | Where-Object {
    Get-ScheduledTask -TaskPath $_.Path -TaskName $_.Name -ErrorAction SilentlyContinue
} | Select-Object -First 1
if (-not $found) {
    Write-Output "$taskPath$(Get-TaskName $Name) isn't installed."
    exit 3
}
$taskPath = $found.Path
$task = $found.Name
if ($PSCmdlet.ShouldProcess("$taskPath$task", 'Restart')) {
    Stop-ScheduledTask -TaskPath $taskPath -TaskName $task -ErrorAction SilentlyContinue
    Get-CimInstance Win32_Process -Filter "Name like 'python%'" |
        Where-Object { Test-InstanceCommandLine $Name $_.CommandLine } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-ScheduledTask -TaskPath $taskPath -TaskName $task
    Write-Output "Restarted $taskPath$task"
}
exit 0
