# Scenario tests for scripts/restart_instance.ps1: which running processes
# count as the instance being restarted. The script's functions are loaded
# from its AST (its main code doesn't run); prints "PASS <name>" per check
# and exits 1 on the first failure. Run by tests/test_preflight.py.
param([Parameter(Mandatory = $true)][string]$Script)
$ErrorActionPreference = 'Stop'

$ast = [System.Management.Automation.Language.Parser]::ParseFile($Script, [ref]$null, [ref]$null)
$functions = $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $true)
foreach ($f in $functions) { . ([scriptblock]::Create($f.Extent.Text)) }

function Check($Name, $Condition) {
    if (-not $Condition) { Write-Output "FAIL $Name"; exit 1 }
    Write-Output "PASS $Name"
}

$main = @(Get-TaskCandidates 'main')
Check 'main: ours first, then the old hand-made task' (($main.Count -eq 2) -and
    ($main[0].Path -eq '\HeyClaude\') -and ($main[0].Name -eq 'Web') -and
    ($main[1].Path -eq '\') -and ($main[1].Name -eq 'Hey Claude Web'))
$mom = @(Get-TaskCandidates 'mom')
Check 'mom: only her family task' (($mom.Count -eq 1) -and ($mom[0].Name -eq 'Family-mom'))
$py = 'C:\hc\.venv\Scripts\python.exe'
Check 'main is the Web task' ((Get-TaskName 'main') -eq 'Web')
Check 'mom is her family task' ((Get-TaskName 'mom') -eq 'Family-mom')
Check 'main: the host supervisor' (Test-InstanceCommandLine 'main' "$py -m assistant.host web")
Check 'main: the web child' (Test-InstanceCommandLine 'main' "$py -m assistant --mode web --host 127.0.0.1 --port 8765")
Check 'main: never a family copy' (-not (Test-InstanceCommandLine 'main' "$py -m assistant --mode web --config config.mom.toml --env-file .env.mom"))
Check 'mom: her copy, not momo' ((Test-InstanceCommandLine 'mom' "$py -m assistant --mode web --config config.mom.toml --env-file .env.mom") -and
    -not (Test-InstanceCommandLine 'mom' "$py -m assistant --mode web --config config.momo.toml --env-file .env.momo"))
Check 'never a side-port candidate' (-not (Test-InstanceCommandLine 'mom' "$py -m assistant --mode web --port 8866 --config config.mom.toml --env-file .env.mom --candidate"))
