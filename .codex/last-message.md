# Round 461 last-patrol handoff (auto-patrol NEW)

- 命令: python examples/dsh-gate-readiness.py
- 退出码: 2（ready=false；持续 timeout）
- 结果: {"gate_url": "http://127.0.0.1:3080/", "ready": false, "status_code": null, "error": "<urlopen error timed out>", "next_recheck": "2026-09-10T16:30:00+08:00", "next_action": "Wait for the dsh web host to return HTTP 200, then rerun this probe."}
- 诊断: Get-NetTCPConnection -LocalPort 3080 -State Listen -> 0；Get-Process dsh -> 0；E:\npm-global\dsh.ps1 已装未启。根因未变(round-382 起): dsh web host 未运行，:3080 loopback SYN 超时。
- 时间门: 方向表 On or after 2026-09-11T10:00:00+08:00 已过；今 2026-09-20T05:20:34+08:00，按要求跑一次 probe(2s timeout，HTTP 仍超时)，结论同前。
- 处置: 按方向表 otherwise append a new evidence note without an early re-probe 已追加 docs/dsh-web-acceptance.md round-461 note 并提交(仅 doc + handoff 两处，record/ 未染指)。不早复探、不启 dsh、不跑 section 4、不发 browser_click。
- 工作树: 仍存 record/ 特征改(M5+??8: CHANGELOG/README/openeyes/__init__/pyproject/tests/test_smoke + docs/RECORD_DESIGN.md + examples/record_demo_*.py + examples/record-out/ + openeyes/record/ + tests/test_record.py)，与本轮无关。
- blocker (high): :3080 无监听者 -> dsh web host 未运行；section 4 不可执行。用户侧待办: (a) 启 dsh web host 绑 :3080 (dsh --profile web) / (b) wsl --shutdown 清陈旧 HNS / (c) 排查 EDR 拦 :3080。
- blocker (medium): docs/dsh-web-acceptance.md 同质 evidence 已连 461 轮，section 4 正文被淹没。本轮未自主重构(方向仅授权 append)。
- 下次动作(事件驱动): 先 Get-NetTCPConnection -LocalPort 3080 -State Listen；有监听者 -> 重跑 probe，ready=true 执行 docs/dsh-web-acceptance.md section 4 两 tab 点击 dry-run；无监听者 -> 不重跑、不新增 evidence。强烈建议更新项目方向表下一步为 当 :3080 出现监听者后重跑 probe 以止每轮定时复探空转与 runbook 增噪(已连 461 轮同结论)。
- 证据: docs/dsh-web-acceptance.md ## Round 461(已提交)。