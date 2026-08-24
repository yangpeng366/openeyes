# OpenEyes AnyVPN Keepalive launcher - 开关式 (toggle)
# 双击快捷方式:
#   * 如果 keepalive 循环已在运行 -> 停止它 (循环结束, AnyVPN 保持现状)
#   * 否则 -> 启动完整循环 (自动重连 + 每 20s 点 reset 保活)
# 高级参数仍可用: -Interval N / -MaxIter N / -DryRun / -Screenshot / -Help

param(
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
    Write-Host "OpenEyes AnyVPN Keepalive launcher (toggle)" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "双击本快捷方式 = 开关: 已在运行就停止, 没运行就启动。"
    Write-Host ""
    Write-Host "可选参数: -Interval N  -MaxIter N  -DryRun  -Screenshot  -Help"
    Write-Host ""
    Write-Host "脚本: $Script"
    exit 0
}

Set-Location $ProjectDir

if (-not $DryRun) {
    # ---- Toggle: 已有 keepalive 在跑就停掉, 不叠加第二个 ----
    $running = Get-CimInstance Win32_Process |
        Where-Object {
            $_.ProcessId -ne $PID -and
            ($_.CommandLine -match "anyvpn_keepalive\.py" -or
             $_.CommandLine -match "launch-anyvpn-keepalive\.ps1")
        }
    if ($running) {
        $ids = ($running | ForEach-Object { $_.ProcessId }) -join ", "
        Write-Host "[openeyes] keepalive 已在运行 (PID $ids) - 正在停止。" -ForegroundColor Yellow
        $running | ForEach-Object {
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        }
        Write-Host "[openeyes] 已停止。AnyVPN 保持现状。" -ForegroundColor Green
        exit 0
    }
}

# ---- 启动 keepalive 循环 ----
$args = @($Script)
if (-not $DryRun) {
    $args += "--go"
    $args += "--bootstrap"   # 自动重连一次; 之后每轮只点 reset 保活
}
if ($DryRun)     { $args += "--dry-run" }
if ($Screenshot) { $args += "--screenshot-dir"; $args += "shots\anyvpn" }
$args += "--interval"; $args += "$Interval"
if ($MaxIter -gt 0) { $args += "--max-iter"; $args += "$MaxIter" }

Write-Host "[openeyes] launching: python $($args -join ' ')" -ForegroundColor Cyan
& python @args
