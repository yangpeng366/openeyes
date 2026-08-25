# OpenEyes Debug-Edge launcher - 一次性启动带 --remote-debugging-port=PORT 的 Edge。
#
# 用法（双击 / 命令行皆可）：
#   pwsh -NoProfile -File examples\launch-debug-edge.ps1
#   pwsh -NoProfile -File examples\launch-debug-edge.ps1 -Port 9222 -Url http://127.0.0.1:3080
#   pwsh -NoProfile -File examples\launch-debug-edge.ps1 -NoSeed   # 不复制现有 Edge 会话
#   pwsh -NoProfile -File examples\launch-debug-edge.ps1 -Stop     # 停掉本脚本启动的 Edge
#
# 依赖：
#   * Python 3.10+ on PATH（提供 eyes CLI）
#   * Microsoft Edge (msedge.exe) 已安装
# 行为：
#   * :Port 已经 LISTEN -> 直接列 tabs 并退出（幂等）
#   * 否则 -> 调用 `eyes browser launch --port <Port>` 并等待 :Port 进入 LISTEN
#   * 验证：运行 `eyes browser tabs --port <Port>` 并把 JSON 写到 .codex/debug-edge-tabs.json
# 退出码：
#   0 成功, 2 :Port 持续不可达, 3 msedge 找不到, 4 启动后 :Port 未 LISTEN

param(
    [int]$Port = 9222,
    [string]$Url = "about:blank",
    [switch]$NoSeed,
    [switch]$Headless,
    [switch]$Stop,
    [int]$WaitSec = 15,
    [switch]$Help
)

$ErrorActionPreference = "Stop"
$ProjectDir = "E:\gitAll\openeyes"
Set-Location $ProjectDir

if ($Help) {
    Get-Content -LiteralPath $PSCommandPath -TotalCount 30
    exit 0
}

function Test-PortOpen {
    param([int]$P)
    try {
        $c = [System.Net.Sockets.TcpClient]::new()
        $iar = $c.BeginConnect("127.0.0.1", $P, $null, $null)
        $ok = $iar.AsyncWaitHandle.WaitOne(1500)
        if (-not $ok) { $c.Close(); return $false }
        $c.EndConnect($iar)
        $c.Close()
        return $true
    } catch {
        return $false
    }
}

if ($Stop) {
    $target = "--remote-debugging-port=$Port"
    $procs = Get-CimInstance Win32_Process -Filter "Name = 'msedge.exe'" |
        Where-Object { $_.CommandLine -match [regex]::Escape($target) }
    if (-not $procs) {
        Write-Host "[openeyes] no msedge.exe with $target found - nothing to stop" -ForegroundColor Yellow
        exit 0
    }
    $procs | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Write-Host "[openeyes] stopped $($procs.Count) msedge.exe process(es) with $target" -ForegroundColor Green
    exit 0
}

if (Test-PortOpen -P $Port) {
    Write-Host "[openeyes] :$Port already LISTEN - skipping launch" -ForegroundColor Green
    & eyes browser tabs --port $Port 2>&1 | Out-String |
        Tee-Object -LiteralPath .codex/debug-edge-tabs.json | Write-Host
    exit $LASTEXITCODE
}

$msedge = (Get-Command msedge.exe -ErrorAction SilentlyContinue)?.Source
if (-not $msedge) {
    $candidates = @(
        "$env:ProgramFiles (x86)\Microsoft\Edge\Application\msedge.exe",
        "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe"
    )
    foreach ($c in $candidates) { if (Test-Path $c) { $msedge = $c; break } }
}
if (-not $msedge) {
    Write-Host "[openeyes] msedge.exe not found - install Microsoft Edge or update PATH" -ForegroundColor Red
    exit 3
}

Write-Host "[openeyes] :$Port closed - launching msedge with $target" -ForegroundColor Cyan
$launchArgs = @(
    "browser", "launch",
    "--url", $Url,
    "--port", "$Port"
)
if ($NoSeed)  { $launchArgs += "--no-seed" }
if ($Headless) { $launchArgs += "--headless" }
& eyes @launchArgs | Out-String | Write-Host
if ($LASTEXITCODE -ne 0) {
    Write-Host "[openeyes] eyes browser launch exited with $LASTEXITCODE - :$Port 持续不可达" -ForegroundColor Red
    exit 2
}

$deadline = (Get-Date).AddSeconds($WaitSec)
while ((Get-Date) -lt $deadline) {
    if (Test-PortOpen -P $Port) {
        Write-Host "[openeyes] :$Port now LISTEN - listing tabs:" -ForegroundColor Green
        & eyes browser tabs --port $Port 2>&1 | Out-String |
            Tee-Object -LiteralPath .codex/debug-edge-tabs.json | Write-Host
        exit 0
    }
    Start-Sleep -Milliseconds 500
}
Write-Host "[openeyes] :$Port still closed after ${WaitSec}s" -ForegroundColor Red
exit 4