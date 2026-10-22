# Scenario tests for scripts/install_windows_service.ps1's legacy web task
# handling. The script's functions are loaded from its AST (its main code
# doesn't run) and Task Scheduler, the network and processes are faked with
# functions, which PowerShell prefers over cmdlets of the same name.
# Run by tests/test_preflight.py when powershell/pwsh is available; prints
# "PASS <name>" per scenario and exits 1 on the first failure.
param([Parameter(Mandatory = $true)][string]$Script)
$ErrorActionPreference = 'Stop'

$ast = [System.Management.Automation.Language.Parser]::ParseFile($Script, [ref]$null, [ref]$null)
$functions = $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $true)
foreach ($f in $functions) { . ([scriptblock]::Create($f.Extent.Text)) }

$repoDir = 'C:\Users\kylec\Claude-Personal-Assistant'
$taskPath = '\Kwil\'
$oldTaskPath = '\HeyClaude\'
$Port = 8765

function New-Action($Execute, $Arguments, $WorkingDirectory) {
    [pscustomobject]@{ Execute = $Execute; Arguments = $Arguments; WorkingDirectory = $WorkingDirectory }
}
function New-Task($Path, $Name, $Action) {
    [pscustomobject]@{ TaskPath = $Path; TaskName = $Name; State = 'Running'; Actions = @($Action) }
}

# --- fakes ---
function Get-ScheduledTask { param([string]$TaskPath, [string]$TaskName, $ErrorAction) $script:tasks }
function Stop-ScheduledTask { param($TaskPath, $TaskName, $ErrorAction) $script:log += "stop $TaskName"; $script:stopTask.Invoke() }
function Start-ScheduledTask { param($TaskPath, $TaskName, $ErrorAction) $script:log += "start $TaskName" }
function Unregister-ScheduledTask { param($TaskPath, $TaskName, $Confirm) $script:log += "unregister $TaskName" }
function Get-NetTCPConnection {
    param($LocalPort, $State, $ErrorAction)
    if ($script:listener) { [pscustomobject]@{ OwningProcess = $script:listener.ProcessId } }
}
function Get-CimInstance { param($ClassName, $Filter) $script:listener }
function Stop-Process { param($Id, [switch]$Force, $ErrorAction) $script:log += "kill $Id"; $script:killed.Invoke() }
function Read-Host { param($Prompt) $script:answer }
function Start-Sleep { param($Seconds) }
function Write-Host { param($Object) }
function Write-Warning { param($Message) $script:log += "warn" }

function Assert($Name, $Condition) {
    if (-not $Condition) { Write-Output "FAIL $Name : $($script:log -join ', ')"; exit 1 }
    Write-Output "PASS $Name"
}

$kyles = New-Task '\' 'Hey Claude Web' (New-Action '.venv\Scripts\python.exe' `
        '-m kwil --mode web --host 127.0.0.1 --port 8765' $repoDir)
$server = [pscustomobject]@{ ProcessId = 4242; Name = 'python.exe'
    CommandLine = '.venv\Scripts\python.exe -m kwil --mode web --host 127.0.0.1 --port 8765' }

# 1. Kyle's task, answer y: stopping the task frees the port -> removed
$script:tasks = @($kyles); $script:answer = 'y'; $script:log = @()
$script:listener = $server; $script:stopTask = { $script:listener = $null }; $script:killed = {}
$ok = Resolve-LegacyWebTask
Assert 'replaced when its port frees' ($ok -and ($script:log -contains 'unregister Hey Claude Web'))

# 2. y, but the task's python outlives Stop-ScheduledTask: killed by port owner, then removed
$script:log = @(); $script:listener = $server; $script:stopTask = {}
$script:killed = { $script:listener = $null }
$ok = Resolve-LegacyWebTask
Assert 'orphaned server on the port is stopped' ($ok -and ($script:log -contains 'kill 4242') -and
    ($script:log -contains 'unregister Hey Claude Web'))

# 3. y, but something else holds the port: old task kept and restarted, Web not installed
$script:log = @(); $script:stopTask = {}; $script:killed = {}
$script:listener = [pscustomobject]@{ ProcessId = 99; Name = 'node.exe'; CommandLine = 'node server.js' }
$ok = Resolve-LegacyWebTask
Assert 'port held by another program keeps the old task' ((-not $ok) -and
    ($script:log -notcontains 'unregister Hey Claude Web') -and ($script:log -contains 'start Hey Claude Web') -and
    ($script:log -notcontains 'kill 99'))

# 4. answer no: nothing touched
$script:log = @(); $script:answer = 'n'; $script:listener = $server
$ok = Resolve-LegacyWebTask
Assert 'no keeps everything' ((-not $ok) -and $script:log.Count -eq 0)

# 5. another project's task with the same command or name is not ours
$script:answer = 'y'; $script:log = @()
$script:tasks = @(
    (New-Task '\' 'Hey Claude Web' (New-Action 'python.exe' '-m kwil --mode web' 'D:\other\project')),
    (New-Task '\' 'Other' (New-Action 'C:\elsewhere\.venv\Scripts\python.exe' '-m kwil --mode web' 'C:\elsewhere')))
Assert 'other folders are ignored' (@(Get-LegacyWebTask).Count -eq 0)

# 6. our own \Kwil\Web (and the pre-rebrand \HeyClaude\Web, which
#    Unregister-HostTask replaces) is never "legacy"; repo found case-insensitively
$script:tasks = @(
    (New-Task '\Kwil\' 'Web' (New-Action "$repoDir\.venv\Scripts\pythonw.exe" "-m kwil.host web 8765" $repoDir)),
    (New-Task '\HeyClaude\' 'Web' (New-Action "$repoDir\.venv\Scripts\pythonw.exe" "-m assistant.host web 8765" $repoDir)),
    (New-Task '\' 'Phone app' (New-Action 'C:\USERS\KYLEC\Claude-Personal-Assistant\.venv\Scripts\python.exe' '-m kwil --mode web' '')))
$found = @(Get-LegacyWebTask)
Assert 'only the hand-made one in this repo' ($found.Count -eq 1 -and $found[0].TaskName -eq 'Phone app')

# 7. a sibling folder whose name starts with the repo's is not the repo
$script:tasks = @((New-Task '\' 'Hey Claude Web' (New-Action "$repoDir-old\.venv\Scripts\python.exe" `
                '-m kwil --mode web' "$repoDir-old")))
Assert 'C:\x\Claude-old is not C:\x\Claude' (@(Get-LegacyWebTask).Count -eq 0)

# 8. a hand-made task from before the rebrand (python -m assistant) is found
$script:tasks = @((New-Task '\' 'Phone app' (New-Action "$repoDir\.venv\Scripts\python.exe" `
                '-m assistant --mode web' $repoDir)))
Assert 'a pre-rebrand hand-made task is found' (@(Get-LegacyWebTask).Count -eq 1)

# 9. no legacy task: nothing asked
$script:tasks = @(); $script:answer = 'should not be asked'; $script:log = @()
Assert 'no legacy task' ((Resolve-LegacyWebTask) -and $script:log.Count -eq 0)
exit 0
