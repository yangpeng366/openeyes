[CmdletBinding()]
param(
    [switch]$DumpConfig
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$python = Get-Command python -ErrorAction SilentlyContinue
$dsh = Get-Command dsh -ErrorAction SilentlyContinue
$pluginPath = Join-Path $env:USERPROFILE '.dsh\profiles\web\node_modules\@deepseek-ai\dsh-mcp-client'
$pluginPackagePath = Join-Path $pluginPath 'package.json'
$pluginVersion = $null
if (Test-Path -LiteralPath $pluginPackagePath) {
    $pluginPackage = Get-Content -LiteralPath $pluginPackagePath -Raw | ConvertFrom-Json
    $pluginVersion = $pluginPackage.version
}

$result = [ordered]@{
    repository = $repoRoot
    python = [bool]$python
    openeyes_mcp_import = $false
    dsh = [bool]$dsh
    dsh_mcp_client = $pluginVersion -eq '0.1.1-rc.2'
    dsh_mcp_client_version = $pluginVersion
    dump_config_requested = [bool]$DumpConfig
    missing_prerequisites = @()
}

if ($python) {
    Push-Location $repoRoot
    try {
        & $python.Source -c 'import openeyes.mcp.server' 2> $null
        $result.openeyes_mcp_import = ($LASTEXITCODE -eq 0)
    }
    finally {
        Pop-Location
    }
}

if (-not $result.python) {
    $result.missing_prerequisites += 'python'
}
if (-not $result.openeyes_mcp_import) {
    $result.missing_prerequisites += 'openeyes_mcp_import'
}
if (-not $result.dsh) {
    $result.missing_prerequisites += 'dsh'
}
if (-not $result.dsh_mcp_client) {
    $result.missing_prerequisites += 'dsh_mcp_client@0.1.1-rc.2'
}

$result.ready = $result.missing_prerequisites.Count -eq 0
if ($result.ready) {
    $result.next_action = 'Run: pwsh -NoProfile -File examples\dsh-preflight.ps1 -DumpConfig; then perform the two-tab browser_click url_contains acceptance.'
}
else {
    $actions = @()
    if (-not $result.python) {
        $actions += 'Install Python 3.11+.'
    }
    if (-not $result.openeyes_mcp_import) {
        $actions += 'From the repository, run: python -m pip install -e .'
    }
    if (-not $result.dsh) {
        $actions += 'Run: npm install -g @deepseek-ai/dsh@0.1.1-rc.2'
    }
    if (-not $result.dsh_mcp_client) {
        $actions += 'Run: dsh plugin --profile web add @deepseek-ai/dsh-mcp-client@0.1.1-rc.2'
    }
    $actions += 'Then rerun: pwsh -NoProfile -File examples\dsh-preflight.ps1'
      $result.next_action = $actions -join "`n"
}
$result | ConvertTo-Json -Compress

if (-not $result.ready) {
    exit 2
}

if ($DumpConfig) {
    Push-Location $repoRoot
    try {
        & $dsh.Source --profile web --dump-config
        if ($LASTEXITCODE -ne 0) {
            exit $LASTEXITCODE
        }
    }
    finally {
        Pop-Location
    }
}
