# OpenEyes AnyVPN Keepalive launcher - 开关式 (toggle)
# 双击快捷方式:
#   * 如果 keepalive 循环已在运行 -> 停止它, 断开 AnyVPN, 退出
#   * 否则 -> 启动完整循环 (自动重连 + 倒计时到期前点 reset 保活)
# 高级参数仍可用: -Interval N / -MaxIter N / -DryRun / -Screenshot / -Help

param(
    [int]$Interval = 20,
    [int]$Trigger = 10,
    [int]$MaxIter = 0,
    [switch]$DryRun,
    [switch]$Screenshot,
    [switch]$Help
)

$ErrorActionPreference = "Stop"
$ProjectDir = "E:\gitAll\openeyes"
$Script = Join-Path $ProjectDir "examples\anyvpn_keepalive.py"
$DisconnectScript = Join-Path $ProjectDir "examples\anyvpn_disconnect.py"

if ($Help) {
    Write-Host "OpenEyes AnyVPN Keepalive launcher (toggle)" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "双击本快捷方式 = 开关:"
    Write-Host "  第一次: 启动保活循环 (自动重连 + 到期前点 reset)"
    Write-Host "  再一次 : 停止保活 + 断开 AnyVPN 连接并退出"
    Write-Host ""
    Write-Host "可选参数: -Interval N  -Trigger N  -MaxIter N  -DryRun  -Screenshot  -Help"
    Write-Host ""
    Write-Host "  -Interval : 会话掉线时的重试间隔 (秒, 默认 20)"
    Write-Host "  -Trigger  : 倒计时剩多少秒时点 reset (默认 10, 越大越安全、点得越勤)"
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
        Write-Host "[openeyes] 已停止保活循环，正在断开 AnyVPN..." -ForegroundColor Yellow
        Start-Sleep -Milliseconds 500
        # 真正点 Disconnect, 否则隧道会一直挂着
        & python $DisconnectScript
        Write-Host "[openeyes] 已断开并退出。" -ForegroundColor Green
        exit 0
    }
}

# ---- 启动 keepalive 循环 ----
$pyArgs = @($Script)
if (-not $DryRun) {
    $pyArgs += "--go"
    $pyArgs += "--bootstrap"   # 掉线时自动重连 (会改变网络路由, 所以是显式开关)
}
if ($DryRun)     { $pyArgs += "--dry-run" }
if ($Screenshot) { $pyArgs += "--screenshot-dir"; $pyArgs += "shots\anyvpn" }
$pyArgs += "--interval"; $pyArgs += "$Interval"
$pyArgs += "--trigger";  $pyArgs += "$Trigger"
if ($MaxIter -gt 0) { $pyArgs += "--max-iter"; $pyArgs += "$MaxIter" }

Write-Host "[openeyes] launching: python $($pyArgs -join ' ')" -ForegroundColor Cyan
& python @pyArgs
