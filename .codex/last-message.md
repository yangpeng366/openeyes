## 本轮推进
- 跑 python examples\dsh-gate-readiness.py；exit 2，ready=false（http://127.0.0.1:3080/ timed out）。:3080 仍无监听者，0 个 dsh 进程；根因同 round-382 起。
- docs/dsh-web-acceptance.md 追加 Round 464 evidence note 并提交（commit 本轮）：docs-only 改动，无 source 变更。
- .codex/last-message.md 同步刷新 round-464 handoff；本轮按 460/458 惯例一并入库（上轮 463 漏提交 last-message.md，本次已补齐）。

## 下一轮要看
- 事件驱动复探：先 Get-NetTCPConnection -LocalPort 3080 -State Listen；有监听者 -> 重跑 probe，ready=true 即执行 docs/dsh-web-acceptance.md section 4 两 tab 点击 dry-run；无监听者 -> 按方向表 otherwise 仍追加 note（除非 planner 已改方向表为事件驱动）。
- 强烈建议：planner 将方向表下一步改为「当 :3080 出现监听者后重跑 probe」以停每轮定时复探空转 + runbook 增噪；或授权一次性归档历史 Round note（已连 464 轮同结论）。
- 与本轮无关的 record/ 特征改（M5+??8：CHANGELOG/README/openeyes/__init__/pyproject/tests/test_smoke + docs/RECORD_DESIGN.md + examples/record_demo_*.py + examples/record-out/ + openeyes/record/ + tests/test_record.py）仍挂工作树；不在方向表授权范围内，本轮不染指。

## blocker
- (high) :3080 无监听者 → section 4 不可执行；用户侧待办：dsh --profile web 绑 :3080 / wsl --shutdown 清陈旧 HNS / 排查 EDR 拦 :3080。
- (medium) runbook 同质 evidence 已连 464 轮，section 4 正文被淹没；建议改方向表为事件驱动，或授权归档历史 note。

## 命令复盘（实测）
- python examples\dsh-gate-readiness.py → {"gate_url":"http://127.0.0.1:3080/","ready":false,"status_code":null,"error":"<urlopen error timed out>","next_recheck":"2026-09-10T16:30:00+08:00","next_action":"Wait for the dsh web host to return HTTP 200, then rerun this probe."}；exit 2。
- Get-NetTCPConnection -LocalPort 3080 -State Listen → 0。
- Get-Process -Name dsh → 0。
- 时间: 2026-09-20T06:21:10+08:00