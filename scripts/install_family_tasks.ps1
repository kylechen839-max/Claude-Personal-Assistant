<#
.SYNOPSIS
    Start the family members' own copies of Hey Claude (Mom's, Kai's, ...)
    automatically when you sign in to Windows.
.DESCRIPTION
    Finds every separate instance in the project folder: a config.<name>.toml
    (config.mom.toml, config.kai.toml, ...; *.local.toml doesn't count) with
    its .env.<name> next to it. For each one it registers a Task Scheduler
    task \HeyClaude\Family-<name> that runs, hidden, at sign-in:

        python -m assistant --mode web --config config.<name>.toml --env-file .env.<name>

    and restarts it a minute after a crash. Its log is the one its config
    names (e.g. logs\mom.log). Your own app is install_windows_service.ps1's
    job and is left alone.

    A copy already running in a console window (started by hand) is stopped
    first, so two copies never fight over the port; that window can then be
    closed. No administrator rights needed. Windows PowerShell 5.1 and 7.

        .\scripts\install_family_tasks.ps1              # install and start
        .\scripts\install_family_tasks.ps1 -Restart     # restart them (after an update)
        .\scripts\install_family_tasks.ps1 -Uninstall   # stop and remove the tasks

    A task starts in your normal Windows environment. If you have set
    GOOGLE_TOKEN_JSON, WEB_TOKEN or similar as user environment variables,
    the copy refuses to start (so it never sees your accounts) and its log
    says which variable; remove it from the user environment.
#>
[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSAvoidUsingWriteHost', '',
    Justification = 'Interactive installer: output is for the person running it.')]
[CmdletBinding()]
param(
    [switch]$Restart,
    [switch]$Uninstall
)
$ErrorActionPreference = 'Stop'

$repoDir = Split-Path -Parent $PSScriptRoot
$taskPath = '\HeyClaude\'
$prefix = 'Family-'
$pythonw = Join-Path $repoDir '.venv\Scripts\pythonw.exe'
$user = "$env:USERDOMAIN\$env:USERNAME"

function Get-FamilyInstance {
    # @{Name; Config; EnvFile} for each config.<name>.toml with .env.<name>.
    param([string]$Dir = $repoDir)
    Get-ChildItem -LiteralPath $Dir -Filter 'config.*.toml' -File |
        Where-Object { $_.Name -match '^config\.([A-Za-z0-9_-]+)\.toml$' -and $_.Name -notlike '*.local.toml' } |
        ForEach-Object {
            $name = ([regex]::Match($_.Name, '^config\.([A-Za-z0-9_-]+)\.toml$')).Groups[1].Value
            $envFile = ".env.$name"
            if (Test-Path -LiteralPath (Join-Path $Dir $envFile)) {
                [pscustomobject]@{ Name = $name; Config = $_.Name; EnvFile = $envFile }
            }
            else {
                Write-Warning "$($_.Name) has no $envFile next to it; skipped."
            }
        }
}

function Get-InstanceArgument {
    param($Instance)
    "-m assistant --mode web --config $($Instance.Config) --env-file $($Instance.EnvFile)"
}

function Stop-InstanceProcess {
    # Copies of this instance started by hand (python -m assistant ... --config config.<name>.toml).
    [CmdletBinding(SupportsShouldProcess = $true)]
    param($Instance)
    $pattern = '-m\s+assistant\b.*--config\s+"?' + [regex]::Escape($Instance.Config) + '\b'
    $procs = @(Get-CimInstance Win32_Process -Filter "Name like 'python%'" |
            Where-Object { $_.CommandLine -and $_.CommandLine -match $pattern })
    foreach ($p in $procs) {
        if ($PSCmdlet.ShouldProcess("process $($p.ProcessId)", 'Stop')) {
            Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
        }
    }
    return $procs.Count
}

function Unregister-FamilyTask {
    Get-ScheduledTask -TaskPath $taskPath -ErrorAction SilentlyContinue |
        Where-Object { $_.TaskName -like "$prefix*" } |
        ForEach-Object {
            Stop-ScheduledTask -TaskPath $taskPath -TaskName $_.TaskName -ErrorAction SilentlyContinue
            Unregister-ScheduledTask -TaskPath $taskPath -TaskName $_.TaskName -Confirm:$false
            Write-Host "Removed task $taskPath$($_.TaskName)"
        }
}

function Register-FamilyTask {
    param($Instance)
    $name = "$prefix$($Instance.Name)"
    $action = New-ScheduledTaskAction -Execute $pythonw -Argument (Get-InstanceArgument $Instance) `
        -WorkingDirectory $repoDir
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
    $trigger.Delay = 'PT30S'  # after the network (and your own app) are up
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) `
        -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskPath $taskPath -TaskName $name -Action $action -Trigger $trigger `
        -Settings $settings -Principal $principal -Force `
        -Description "Hey Claude for $($Instance.Name): $($Instance.Config) + $($Instance.EnvFile)" | Out-Null
    Start-ScheduledTask -TaskPath $taskPath -TaskName $name
    Write-Host "Installed and started $taskPath$name ($($Instance.Config))"
}

if ($Uninstall) {
    Unregister-FamilyTask
    Write-Host 'The family copies no longer start at sign-in. Start one by hand with:'
    Write-Host '  .\.venv\Scripts\python.exe -m assistant --mode web --config config.<name>.toml --env-file .env.<name>'
    exit 0
}

$instances = @(Get-FamilyInstance)
if ($instances.Count -eq 0) {
    Write-Host 'No family instances found (config.<name>.toml with .env.<name>). Nothing to do.'
    exit 0
}

if ($Restart) {
    foreach ($i in $instances) {
        $name = "$prefix$($i.Name)"
        if (Get-ScheduledTask -TaskPath $taskPath -TaskName $name -ErrorAction SilentlyContinue) {
            Stop-ScheduledTask -TaskPath $taskPath -TaskName $name -ErrorAction SilentlyContinue
            [void](Stop-InstanceProcess $i)
            Start-ScheduledTask -TaskPath $taskPath -TaskName $name
            Write-Host "Restarted $taskPath$name"
        }
        else {
            Write-Warning "$taskPath$name isn't installed; run this script without -Restart first."
        }
    }
    exit 0
}

if (-not (Test-Path -LiteralPath $pythonw)) {
    Write-Error "No .venv found in $repoDir. Create it first (README.md, Quick start)."
    exit 2
}
foreach ($i in $instances) {
    $stopped = Stop-InstanceProcess $i
    if ($stopped) {
        Write-Host "Stopped the copy of $($i.Name)'s app started by hand; you can close its window."
    }
    Register-FamilyTask $i
}
Write-Host ''
Write-Host 'They start each time you sign in to Windows (about 30 s after) and restart'
Write-Host 'a minute after a crash. After updating the code, restart them with:'
Write-Host '  .\scripts\install_family_tasks.ps1 -Restart'
Write-Host 'Status: Task Scheduler -> Task Scheduler Library -> HeyClaude.'
