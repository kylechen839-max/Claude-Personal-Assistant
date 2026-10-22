# Scenario tests for scripts/install_family_tasks.ps1: which instances it
# finds and the command each task runs. The script's functions are loaded
# from its AST (its main code doesn't run); prints "PASS <name>" per check
# and exits 1 on the first failure. Run by tests/test_preflight.py.
param([Parameter(Mandatory = $true)][string]$Script)
$ErrorActionPreference = 'Stop'

$ast = [System.Management.Automation.Language.Parser]::ParseFile($Script, [ref]$null, [ref]$null)
$functions = $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $true)
foreach ($f in $functions) { . ([scriptblock]::Create($f.Extent.Text)) }
function Write-Warning { param($Message) $script:warnings += $Message }

function Check($Name, $Condition) {
    if (-not $Condition) { Write-Output "FAIL $Name"; exit 1 }
    Write-Output "PASS $Name"
}

$dir = Join-Path ([IO.Path]::GetTempPath()) ("family-" + [guid]::NewGuid())
New-Item -ItemType Directory $dir | Out-Null
try {
    'x' | Set-Content (Join-Path $dir 'config.toml')
    'x' | Set-Content (Join-Path $dir 'config.local.toml')
    'x' | Set-Content (Join-Path $dir 'config.mom.toml')
    'x' | Set-Content (Join-Path $dir 'config.mom.local.toml')
    'x' | Set-Content (Join-Path $dir '.env.mom')
    'x' | Set-Content (Join-Path $dir 'config.kai.toml')
    'x' | Set-Content (Join-Path $dir '.env.kai')
    'x' | Set-Content (Join-Path $dir 'config.orphan.toml')
    $script:warnings = @()
    $found = @(Get-FamilyInstance -Dir $dir | Sort-Object Name)
    Check 'finds mom and kai only' (($found.Name -join ',') -eq 'kai,mom')
    Check 'skips a config without its env file, with a warning' ($script:warnings -match 'config.orphan.toml')
    $mom = $found | Where-Object Name -eq 'mom'
    Check 'runs the instance with its own config and env file' (
        (Get-InstanceArgument $mom) -eq '-m kwil --mode web --config config.mom.toml --env-file .env.mom')
}
finally {
    Remove-Item -Recurse -Force $dir
}
