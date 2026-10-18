<#
.SYNOPSIS
    Run Hey Claude around the clock on this Windows PC (the PC as the home host).
.DESCRIPTION
    Registers Task Scheduler tasks (folder \HeyClaude\) that start when you
    sign in, run hidden, restart after a crash and log to logs\:

      Wake  python -m assistant --mode wake   always listening for "Hey Claude"
      Web   python -m assistant --mode web    phone app on 127.0.0.1 (skip: -NoWeb)

    scripts\windows_host.py keeps each one running (restart after 10 s; after
    5 crashes in 5 minutes it waits 5 minutes) and writes logs\host-<mode>.log.

    Then it checks the power settings that would stop the assistant (sleep
    and hibernate on AC power, USB selective suspend for the mic) and offers
    to change them. Nothing is changed without your "y". The display may
    still turn off; that doesn't affect the assistant.

    No administrator rights needed. Works in Windows PowerShell 5.1 and 7.

        .\scripts\install_windows_service.ps1              # install and start
        .\scripts\install_windows_service.ps1 -NoWeb       # wake word only
        .\scripts\install_windows_service.ps1 -PowerOnly   # only the power check
        .\scripts\install_windows_service.ps1 -Uninstall   # stop and remove the tasks
#>
[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSAvoidUsingWriteHost', '',
    Justification = 'Interactive installer: output is for the person running it.')]
[CmdletBinding()]
param(
    [switch]$NoWeb,
    [switch]$PowerOnly,
    [switch]$SkipPower,
    [switch]$Uninstall
)
$ErrorActionPreference = 'Stop'

$repoDir = Split-Path -Parent $PSScriptRoot
$taskPath = '\HeyClaude\'
$pythonw = Join-Path $repoDir '.venv\Scripts\pythonw.exe'
$python = Join-Path $repoDir '.venv\Scripts\python.exe'
$hostScript = Join-Path $repoDir 'scripts\windows_host.py'
$user = "$env:USERDOMAIN\$env:USERNAME"

# powercfg GUIDs: sleep after / hibernate after (SUB_SLEEP), USB selective suspend
$usbGroup = '2a737441-1930-4402-8d77-b2bebba308a3'
$usbSuspend = '48e6b7a6-50f5-4782-a5d4-53bb8f07e226'

function Get-AcIndex {
    param([string]$Group, [string]$Setting)
    $out = & powercfg /query SCHEME_CURRENT $Group $Setting 2>$null | Out-String
    $m = [regex]::Match($out, 'Current AC Power Setting Index:\s*0x([0-9a-fA-F]+)')
    if ($m.Success) { return [Convert]::ToInt32($m.Groups[1].Value, 16) }
    return $null
}

function Format-Duration {
    param($Seconds)
    if ($null -eq $Seconds) { return 'unknown' }
    if ($Seconds -eq 0) { return 'never' }
    return "after $([math]::Round($Seconds / 60)) min"
}

function Invoke-PowerCheck {
    Write-Host "`n== Power settings (plugged in) =="
    $sleep = Get-AcIndex 'SUB_SLEEP' 'STANDBYIDLE'
    $hibernate = Get-AcIndex 'SUB_SLEEP' 'HIBERNATEIDLE'
    $usb = Get-AcIndex $usbGroup $usbSuspend
    Write-Host "  Sleep:                 $(Format-Duration $sleep)"
    Write-Host "  Hibernate:             $(Format-Duration $hibernate)"
    $usbText = switch ($usb) { 0 { 'disabled' } 1 { 'enabled' } default { 'not shown on this PC' } }
    Write-Host "  USB selective suspend: $usbText"

    $changes = @()
    if ($sleep -ne 0) { $changes += 'never sleep when plugged in (powercfg /change standby-timeout-ac 0)' }
    if ($hibernate -ne 0) { $changes += 'never hibernate when plugged in (powercfg /change hibernate-timeout-ac 0)' }
    if ($usb -eq 1) { $changes += 'turn off USB selective suspend when plugged in, so a USB mic is never powered down' }

    Write-Host "`n  Sleep stops the assistant completely: no wake word, no phone app."
    Write-Host '  Note: a desktop left on idles at roughly 30-80 W (a gaming PC with a big'
    Write-Host '  GPU toward the top), i.e. about 0.7-2 kWh a day, 20-90 USD a year at'
    Write-Host '  0.15 USD/kWh. A Raspberry Pi 5 idles at about 3-5 W. A plug-in power'
    Write-Host '  meter gives your real figure.'

    if ($changes.Count -eq 0) {
        Write-Host "`n  Nothing to change."
        return
    }
    Write-Host "`n  Proposed changes (the display can still turn off):"
    $changes | ForEach-Object { Write-Host "    - $_" }
    $answer = Read-Host '  Apply these changes? [y/N]'
    if ($answer -notmatch '^(y|yes)$') {
        Write-Host '  Left unchanged.'
        return
    }
    $failed = 0
    if ($sleep -ne 0) { & powercfg /change standby-timeout-ac 0; if ($LASTEXITCODE) { $failed++ } }
    if ($hibernate -ne 0) { & powercfg /change hibernate-timeout-ac 0; if ($LASTEXITCODE) { $failed++ } }
    if ($usb -eq 1) {
        & powercfg /setacvalueindex SCHEME_CURRENT $usbGroup $usbSuspend 0; if ($LASTEXITCODE) { $failed++ }
        & powercfg /setactive SCHEME_CURRENT; if ($LASTEXITCODE) { $failed++ }
    }
    if ($failed) {
        Write-Host '  powercfg reported an error; change it in Settings -> System -> Power instead.'
    }
    else {
        Write-Host '  Done.'
    }
    Write-Host '  Also check: Device Manager -> your USB mic (or its USB hub) -> Power'
    Write-Host '  Management -> untick "Allow the computer to turn off this device".'
}

function Stop-HostProcess {
    [CmdletBinding(SupportsShouldProcess = $true)]
    param()
    # The supervisor and the assistant it started (both run from this folder).
    Get-CimInstance Win32_Process -Filter "Name like 'python%'" |
        Where-Object { $_.CommandLine -and $_.CommandLine.Contains($repoDir) -and
            ($_.CommandLine -match 'windows_host\.py' -or $_.CommandLine -match '-m assistant --mode') } |
        ForEach-Object {
            if ($PSCmdlet.ShouldProcess("process $($_.ProcessId)", 'Stop')) {
                Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
            }
        }
}

function Unregister-HostTask {
    foreach ($name in 'Wake', 'Web') {
        $task = Get-ScheduledTask -TaskPath $taskPath -TaskName $name -ErrorAction SilentlyContinue
        if ($task) {
            Stop-ScheduledTask -TaskPath $taskPath -TaskName $name -ErrorAction SilentlyContinue
            Unregister-ScheduledTask -TaskPath $taskPath -TaskName $name -Confirm:$false
            Write-Host "Removed task $taskPath$name"
        }
    }
    Stop-HostProcess
}

function Register-HostTask {
    param([string]$Name, [string]$Mode, [string]$Description)
    $action = New-ScheduledTaskAction -Execute $pythonw -Argument "`"$hostScript`" $Mode" `
        -WorkingDirectory $repoDir
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
    $trigger.Delay = 'PT20S'  # let audio devices and the network come up
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) `
        -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskPath $taskPath -TaskName $Name -Action $action -Trigger $trigger `
        -Settings $settings -Principal $principal -Description $Description -Force | Out-Null
    Start-ScheduledTask -TaskPath $taskPath -TaskName $Name
    Write-Host "Installed and started $taskPath$Name (log: logs\host-$Mode.log)"
}

if ($Uninstall) {
    Unregister-HostTask
    Write-Host 'Hey Claude no longer starts at sign-in. Power settings were left as they are.'
    exit 0
}

if (-not $PowerOnly) {
    if (-not (Test-Path -LiteralPath $pythonw)) {
        Write-Error "No .venv found in $repoDir. Create it first (README.md, Quick start)."
        exit 2
    }
    if (-not (Test-Path -LiteralPath (Join-Path $repoDir '.env'))) {
        Write-Warning '.env is missing; the assistant will fail until you add your keys.'
    }
    $help = & $python -m assistant --help 2>&1 | Out-String
    $hasWeb = $help -match '--mode \{[^}]*\bweb\b'

    Write-Host '== Stopping any running copy started by this script =='
    Unregister-HostTask
    Register-HostTask -Name 'Wake' -Mode 'wake' -Description 'Hey Claude: always-listening wake word (python -m assistant --mode wake)'
    if ($hasWeb -and -not $NoWeb) {
        Register-HostTask -Name 'Web' -Mode 'web' -Description 'Hey Claude: phone web app on 127.0.0.1 (python -m assistant --mode web)'
    }
    Write-Host ''
    Write-Host 'They start each time you sign in to Windows. After a reboot nothing runs'
    Write-Host 'until someone signs in; to start without signing in, set up automatic'
    Write-Host 'sign-in for this account (Windows Settings -> Accounts -> Sign-in options).'
    Write-Host 'Status: Task Scheduler -> Task Scheduler Library -> HeyClaude.'
    Write-Host 'Only one place should listen for "Hey Claude": stop the Pi service if you have one.'
}

if (-not $SkipPower) {
    Invoke-PowerCheck
}
