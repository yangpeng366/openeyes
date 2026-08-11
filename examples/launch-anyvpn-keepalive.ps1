# OpenEyes AnyVPN Keepalive launcher
# Default: real click loop (--go), 20-second interval.
# Pass --bootstrap once at startup if VPN is disconnected.

param(
    [switch]$Bootstrap,
    [int]$Interval = 20,
    [int]$MaxIter = 0,
    [switch]$DryRun,
    [switch]$Screenshot,
    [switch]$Help
)

$ErrorActionPreference = "Stop"
$ProjectDir = "E:\gitAll\openeyes"
$Script = Join-Path $ProjectDir "examples\anyvpn_keepalive.py"

if ($Help) {
    Write-Host "OpenEyes AnyVPN Keepalive launcher" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Usage: launch-anyvpn-keepalive.ps1 [-Bootstrap] [-Interval N] [-MaxIter N] [-DryRun] [-Screenshot]"
    Write-Host ""
    Write-Host "Examples:"
    Write-Host "  launch-anyvpn-keepalive.ps1                    # default: --go --interval 20 (keepalive loop)"
    Write-Host "  launch-anyvpn-keepalive.ps1 -Bootstrap         # first run: click Secure my connection, then keepalive"
    Write-Host "  launch-anyvpn-keepalive.ps1 -DryRun           # print target, do not click"
    Write-Host "  launch-anyvpn-keepalive.ps1 -Interval 30      # slower interval"
    Write-Host "  launch-anyvpn-keepalive.ps1 -MaxIter 5        # bounded test (5 iterations)"
    Write-Host "  launch-anyvpn-keepalive.ps1 -Screenshot       # save before/after screenshots per click"
    Write-Host ""
    Write-Host "Script path: $Script"
    exit 0
}

Set-Location $ProjectDir

$args = @($Script)
if ($Bootstrap) { $args += "--bootstrap" }
if ($DryRun)     { $args += "--dry-run" }
if ($Screenshot) { $args += "--screenshot-dir"; $args += "shots\anyvpn" }
$args += "--interval"; $args += "$Interval"
if ($MaxIter -gt 0) { $args += "--max-iter"; $args += "$MaxIter" }

if (-not $DryRun) { $args += "--go" }

Write-Host "[openeyes] launching: python $($args -join ' ')" -ForegroundColor Cyan
& python @args