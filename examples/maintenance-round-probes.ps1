[CmdletBinding()]
param(
    [ValidateSet('List','Run')][string]$Mode = 'List',
    [string]$ReportPath
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$scheduledFor = '2026-09-04T18:00:00+08:00'

$probeOrder = @(
    'git_state',
    'pytest_suite',
    'dsh_preflight',
    'browser_gate',
    'skill_hash'
)

$probeCommands = [ordered]@{
    git_state = 'git rev-parse --abbrev-ref HEAD; git rev-parse HEAD; git rev-parse origin/main; git status --porcelain=v1 --branch; git rev-list --left-right --count origin/main...HEAD'
    pytest_suite = 'python -m pytest tests/ -q --no-header'
    dsh_preflight = 'pwsh -NoProfile -File examples\dsh-preflight.ps1'
    browser_gate = 'Get-NetTCPConnection -State Listen | Where-Object { $_.LocalPort -in 9222,3080 }; python -m openeyes.cli.main windows list --title-contains Edge'
    skill_hash = 'Get-FileHash -LiteralPath skills\openeyes\SKILL.md,E:\AI-Portable\codex-home\skills\openeyes\SKILL.md -Algorithm SHA256'
}

function Invoke-CommandLine {
    param([string]$CommandLine)
    $LASTEXITCODE = 0
    $output = & ([scriptblock]::Create($CommandLine)) 2>&1
    $exitCode = $LASTEXITCODE
    if ($null -eq $exitCode) { $exitCode = 0 }
    $text = ($output | Out-String -Width 200).TrimEnd()
    [pscustomobject]@{ ok = ($exitCode -eq 0); exit_code = $exitCode; output = $text }
}

if ($Mode -eq 'List') {
    $probeList = foreach ($id in $probeOrder) {
        [ordered]@{ id = $id; command = $probeCommands[$id] }
    }
    [ordered]@{
        mode = 'list'
        scheduled_for = $scheduledFor
        note = 'Default List mode prints the probe set only; Run mode requires -ReportPath.'
        probes = $probeList
    } | ConvertTo-Json -Depth 5
    return
}

if ([string]::IsNullOrWhiteSpace($ReportPath)) {
    throw 'Run mode requires -ReportPath so probe output is captured to a file.'
}

$results = [ordered]@{}
foreach ($id in $probeOrder) {
    $command = $probeCommands[$id]
    $probeStart = Get-Date
    try {
        $probe = Invoke-CommandLine -CommandLine $command
    }
    catch {
        $probe = [pscustomobject]@{ ok = $false; exit_code = -1; output = $_.ToString() }
    }
    $results[$id] = [ordered]@{
        command = $command
        ok = $probe.ok
        exit_code = $probe.exit_code
        duration_ms = [int]((Get-Date) - $probeStart).TotalMilliseconds
        output = $probe.output
    }
}

$report = [ordered]@{
    mode = 'run'
    generated_at = (Get-Date).ToString('o')
    repo_head = (git rev-parse HEAD)
    repo_origin_main = (git rev-parse origin/main)
    probes = $results
}

$dir = Split-Path -Parent $ReportPath
if ($dir -and -not (Test-Path -LiteralPath $dir)) {
    New-Item -ItemType Directory -Path $dir -Force | Out-Null
}
$reportJson = $report | ConvertTo-Json -Depth 6
[System.IO.File]::WriteAllText($ReportPath, $reportJson, [System.Text.UTF8Encoding]::new($false))
$reportJson
return
