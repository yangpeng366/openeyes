<#
.SYNOPSIS
  Run the OpenEyes quality gate locally: lint, tests, encoding sweep, MCP contract.

.DESCRIPTION
  Mirrors .github/workflows/ci.yml step for step. A push is "ready" when this
  script exits 0. Pass -SkipTests to run only the fast checks.

.EXAMPLE
  pwsh -NoProfile -File examples\quality-gate.ps1
#>
[CmdletBinding()]
param(
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot

$failures = @()

function Invoke-Step {
    param(
        [string]$Name,
        [scriptblock]$Action
    )
    Write-Host ""
    Write-Host "==> $Name" -ForegroundColor Cyan
    & $Action
    if ($LASTEXITCODE -ne 0) {
        $script:failures += $Name
        Write-Host "    FAILED (exit $LASTEXITCODE)" -ForegroundColor Red
    }
    else {
        Write-Host "    ok" -ForegroundColor Green
    }
}

try {
    Invoke-Step "lint (ruff)" { python -m ruff check . }
    Invoke-Step "encoding sweep (no BOM)" { python examples\bom-sweep.py }
    Invoke-Step "MCP stdio contract" { python examples\mcp-stdio-probe.py }
    if (-not $SkipTests) {
        Invoke-Step "tests (pytest)" { python -m pytest tests/ -q }
    }
}
finally {
    Pop-Location
}

Write-Host ""
if ($failures.Count -gt 0) {
    Write-Host "quality gate FAILED: $($failures -join ', ')" -ForegroundColor Red
    exit 1
}
Write-Host "quality gate passed" -ForegroundColor Green
exit 0