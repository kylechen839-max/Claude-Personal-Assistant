# Session watch with a Codex summary (Windows PowerShell 5.1 or 7).
#
#   powershell -ExecutionPolicy Bypass -File run-codex-watch.ps1 [-Minutes 5]
#
# Every -Minutes it runs session_watch.py once (detection, desktop
# notification, watch.log). When a block is new and the Codex CLI is on
# PATH, Codex (read-only sandbox) follows AGENTS.md to summarise the blocked
# sessions into codex-summary.log. Without Codex it just skips the summary.
# Nothing here answers or approves a Claude Code prompt.
param([ValidateRange(1, 1440)][int]$Minutes = 5)

$here = $PSScriptRoot
$watch = Join-Path $here 'session_watch.py'
if (Get-Command python -ErrorAction SilentlyContinue) {
    $py = 'python'; $pyArgs = @()
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    $py = 'py'; $pyArgs = @('-3')
} else {
    Write-Error 'run-codex-watch: Python 3.10+ is not on PATH'
    exit 1
}
$root = if ($env:CLAUDE_CONFIG_DIR) { $env:CLAUDE_CONFIG_DIR } else { Join-Path $HOME '.claude' }
$state = Join-Path $root 'session-watch'
$summary = Join-Path $state 'codex-summary.log'
New-Item -ItemType Directory -Force -Path $state | Out-Null

while ($true) {
    $out = (& $py @pyArgs $watch --once --json) -join "`n"
    if ($out -match '"new": true' -and (Get-Command codex -ErrorAction SilentlyContinue)) {
        if ((Test-Path $summary) -and (Get-Item $summary).Length -gt 1MB) {
            Move-Item -Force $summary "$summary.1"
        }
        Add-Content -Path $summary -Encoding utf8 -Value ("`n== {0} ==" -f (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ'))
        & codex exec --sandbox read-only --cd $here 'Follow AGENTS.md and summarise the blocked Claude Code sessions.' 2>&1 |
            ForEach-Object { "$_" } | Add-Content -Path $summary -Encoding utf8
    }
    Start-Sleep -Seconds ($Minutes * 60)
}
