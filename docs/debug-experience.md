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
## Provenance



- 2026-09-22：DSH 阻塞连 75+ 轮后首次系统沉淀 debug 经验；201 passed, 1 deselected（历史 flaky 单独排除）。