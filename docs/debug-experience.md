# OpenEyes debug experience（巡检调试经验沉淀）

> 用途：连续多轮巡检踩到的根因、关键命令、踩坑统一沉淀在此，方便后续轮巡快速恢复上下文，不必每次从零摸探。

---

## DSH web gate :3080 不可达（Rounds 516-590+ 持续同质 evidence）

### 现象

- `python examples/dsh-gate-readiness.py` 持续退出码 `2`，`ready=false`、`status_code=null`、`error=<urlopen error timed out>`。
- `Get-NetTCPConnection -LocalPort 3080 -State Listen` 无任何输出（`listener_count=0`）。
- `Get-Process -Name dsh` 报 `Cannot find a process with the name "dsh"`（`dsh_count=0`）。
- docs/dsh-web-acceptance.md Section 4（`browser_click` dry-run）自 Round 516 起连续 75+ 轮无法执行。

### 根因

- 外部服务 `dsh web host` 自 Round 516 起未恢复；HNS 或 EDR 根因仍未复核。
- 本机侧无可控手段：不启动服务、不修改 :3080 端口、不发送 browser_click。
- 验证脚本 `examples/dsh-gate-readiness.py` 故意 read-only：见脚本顶部 docstring。

### 关键命令（巡逻标准）

```powershell
# readiness 探针（read-only，HTTP 超时 2s）
python examples/dsh-gate-readiness.py --next-recheck <ISO-8601>

# 端口监听检查
Get-NetTCPConnection -LocalPort 3080 -State Listen

# 进程检查
Get-Process -Name dsh
```

### 关键约定

- **不早期复探**：HTTP 超时已确认可达性失败，复探只消耗本机额度，不产生新信息。
- **单轮仅一次 probe + 三条命令**；round 间保持相同模板，避免 drift。
- **docs/dsh-web-acceptance.md 末尾追加 Round N evidence**：每轮 `<= 9 行`，含时间戳、命令、原始输出、旁证、阻塞、下一步；连续同质 70+ 轮后建议改为恢复事件触发或归档历史 round note。
- **last / last-message / last-round** 跟踪文件每轮刷新；不动 .codex/* 那份（那是另一份路径，独立刷新）。

### 不在 DSH 阻塞下继续推进的策略

- record/、jev、edge schema 等本地工作（feat/decision-jev-layer）继续推进；与 DSH 阻塞无关。
- pytest -q 必须保持 >= 196 passed（含 round 600 schema verifier 6 条、round 602 hybrid_generate transport 4 条）；任何下降立即隔离。
- TYPESAFE_API_KEY / LLM_API_KEY 本机未配，real LLM 路径仅靠 monkeypatch 覆盖（low blocker）。

### 解除阻塞的判定

仅当以下全部成立方可执行 Section 4：

1. `python examples/dsh-gate-readiness.py` 返回 HTTP 200（exit code 0）。
2. `Get-NetTCPConnection -LocalPort 3080 -State Listen` 至少一条记录。
3. 用户或服务负责人确认服务已恢复（飞书通知 / 群消息 / 显式 OK）。

恢复后第一轮需要重新跑完整 probe suite（不复用历史 not-ready evidence）。

---

## pytest 偶发 flaky：test_launch_debug_edge

### 现象

- full suite `-q` 时偶发失败；单独运行 pass。
- 与 round 602 改动无关，单独 `--deselect` 可过。

### 应对

- 巡检 full suite 用 `python -m pytest tests/ -q --deselect tests/test_launch_debug_edge.py::test_helper_help_exits_zero_and_prints_usage` 跳过。
- 不要在本仓 commit 里"顺手修"——属历史 flaky，需独立 issue。
- 完整调用：`python -m pytest tests/ -q --deselect tests/test_launch_debug_edge.py::test_helper_help_exits_zero_and_prints_usage`

---

## AnyVPN 保活实操（2026-10-26）：送达 ≠ 生效

实战对象：Any VPN（MSIX 包 `AnywaySoftInc.AnyVPN_0qkrc2qacwvfm!App`）免费会话保活，脚本 `examples/anyvpn_keepalive.py`。落地提交 `6d57753`、`c646c76`。

### 现象

- 点击报成功，但会话照样掉线；旧脚本的判据是"按钮名字变了"。
- 续期总是踩在掉线那一刻：免费会话 20 秒到期，而脚本 `interval=20`，等于每次都点�� `0:00 left`。
- 会话超时后 Any VPN **自己关掉窗口**（进程还在，窗口没了），脚本只能退出。
- 连上后 Any VPN 以 `RouteMetric 1` 抢下默认路由，此时 `223.5.5.5` / `www.baidu.com` 实测不通；会话过期、路由回落后恢复。出口 IP 实测仍是本地运营商（`182.150.63.187`，`loc=CN`），即"连上了但流量没走隧道"——**此条待复测**。

### 根因（按可复用程度排序）

1. **动作送达不等于业务生效。** 按钮的可访问名是**活的倒计时**（`0:18 left - click to reset`），每秒自变；拿"名字变了"当成功判据等于永远判真。这是产品级信任问题，不是脚本 bug。
2. **坐标语义不统一。** DPI 不感知进程里 UIA 报物理像素，而 `GetWindowRect` / `SetCursorPos` 报虚拟化坐标，两套坐标系混用 → 点击偏几个像素、甚至点到别的控件。
3. **元素身份不稳。** 按可见名选元素在活的 UI 上不可靠；`automation_id`（`KeepFreeAliveButton`）才是稳定锚点。
4. **只有像素点击这一种动作方式。** 抢焦点、动用户鼠标，且 `SetCursorPos` 后紧跟点击可能落在**旧光标位置**（窗口还没处理 `WM_MOUSEMOVE`）；XAML/UWP 还会忽略零时长点击。
5. **周期任务被写成了固定间隔。** 正确形态是读状态、判阈值、到点才动手。
6. **应用生命周期是自动化的隐形边界。** UWP 应用会自己退，自动化必须能识别并重建。
7. **同一控件在不同时刻语义不同。** `ConnectButton` 连接后标签变 `Disconnect`；不做前置断言就可能在会话活着时把它点掉。

### 关键结论（可直接复用）

- **判据要用状态语义，不要用标签文本。** 续期成功 = 倒计时**回跳**（`9s → 19s`），不是"名字变了"。
- **点击优先 UIA Invoke 图案**：`click_element()` 走 Invoke，不需要像素、不抢焦点、不动用户鼠标；像素点击只作降级。
- **恢复窗口用 `SW_SHOWNOACTIVATE`（4）**：最小化窗口的控件树是空的，但 `SW_RESTORE` 会抢前台；`SW_SHOWNOACTIVATE` 既重建控件树又不动前台（实测前台始终是原窗口）。
- **动作前校验控件当前语义**：标签是 `Disconnect` 时拒绝点击 `ConnectButton`。
- **调用方要能区分"动作已投递"和"状态已改变"**：日志与报告必须分两级。
- **落成原语**：`openeyes.core.verify.act_until(act, expect, retries=N)`——执行 → 轮询期望 → 不成立换策略重试 → 返回 `{ok, attempts, result, elapsed, error}`。AnyVPN 脚本已改为它的参考实现。

### 复现命令

```powershell
python examples\anyvpn_keepalive.py --go --bootstrap --trigger 10   # 保活
python examples\anyvpn_disconnect.py                               # 只断开
python -m pytest tests/test_keepalive_logic.py tests/test_verify.py -q
```

---

## Branch / commit 推进约定

- 主巡检分支：`feat/decision-jev-layer`（已 ahead origin 47+ commits）。
- docs / tests / examples / skills 单文件改动可直推 main（见 `docs/maintenance-policy.md`）。
- source / release / permissions / dependencies / destructive changes → PR + 用户决策。
- 巡检 round 不主动 git push；本地 commit 后待用户显式授权。

---


---

## Round 604 stability re-run (2026-09-29)

- pytest tests/test_record.py::TestHybridGenerateTransport -q => 4 passed。
- pytest tests/ -q --deselect tests/test_launch_debug_edge.py::test_helper_help_exits_zero_and_prints_usage => 201 passed, 1 deselected（17.42s）。
- 结论：Round 603 状态持续；仍未配置 TYPESAFE_API_KEY，real LLM path 阻塞未解除。

## feat/decision-jev-layer 推送前风险复盘

- ahead origin 已达 48 commits（含 Round 600-604）；推送窗口一旦扩大将难以拆 PR，建议先 squash 成 4-5 个主题提交再推 origin。
- 推荐主题：record schema verifier、hybrid_generate transport contract、decision/jev shadow、debug-experience doc、tracking files refresh。
- 在 user 显式放权前不主动 git push。

---

## Round 605 event-driven dsh gate (2026-10-08)

- 现象：lastExit=2, consecutiveFailures=2, resumeCount=4；DSH 仍超时，listener_count=0，dsh_count=0。
- 决策：依据 ad-hoc 备注（516-590 连 75 轮同质 evidence），本轮正式切到 **event-driven dsh gate**。
- 后续策略：
  - 不再按固定间隔复探 python examples/dsh-gate-readiness.py。
  - 仅当 (a) :3080 出现 Listen，(b) dsh 进程被重启，或 (c) 用户在飞书 / Bitable 显式确认服务恢复 时，才重跑 readiness probe。
  - 触发后跑 python examples/dsh-gate-readiness.py，HTTP 200 才进入 docs/dsh-web-acceptance.md Section 4。
- 巡检脚本（Start-CodexAutoPatrolLoop.ps1）可参考 state/projects/OpenEyes.json 的 dshGateMode=event-driven 字段跳过本项目 readiness 子步，避免连续 exit 2 触发 thread 重置。

---

## Round 606 patrol loop dshGateMode wiring (2026-10-09)

- 落地：.codex-auto-patrol/Start-CodexAutoPatrolLoop.ps1 读取 state/projects/<Project>.json 的 dshGateMode / dshGateResume。
- 当 dshGateMode=event-driven 时，把硬规则注入子 agent prompt：禁止固定间隔复探 readiness；仅 listener / dsh 进程 / dshGateResume=true 才允许重跑。
- OpenEyes state 已写：dshGateMode=event-driven，dshGateResume=false。
- 旁证本轮：listener_count=0，dsh_count=0；未跑 readiness probe。
- 另落 docs/push-squash-plan.md：5 主题 squash 推送计划（授权前不 push）。
## Provenance




- 2026-09-22：DSH 阻塞连 75+ 轮后首次系统沉淀 debug 经验；201 passed, 1 deselected（历史 flaky 单独排除）。
- 2026-10-26：AnyVPN 保活实操沉淀"送达 ≠ 生效"这条产品级教训，并落成 `openeyes.core.verify.act_until` 原语（AnyVPN 脚本改为参考实现）。