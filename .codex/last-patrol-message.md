# Round 414 last-patrol handoff (mirror)

- 命令: python examples\dsh-gate-readiness.py
- 退出码: 2（ready=false；持续 timeout）
- 结果: {"gate_url": "http://127.0.0.1:3080/", "ready": false, "status_code": null, "error": "<urlopen error timed out>", "next_recheck": "2026-09-10T16:30:00+08:00", "next_action": "Wait for the dsh web host to return HTTP 200, then rerun this probe."}
- 诊断: Get-NetTCPConnection -LocalPort 3080 -State Listen -> NO_LISTENER_ON_3080 (0)；Get-Process -Name dsh -> NO_DSH_PROCESS (0)。根因未变（round-382）。
- 时间门: 方向表要求"On or after 2026-09-11T10:00:00+08:00"重跑；今 2026-09-19T13:40:49+08:00，门已过，结论同前。
- 处置: 按方向表"otherwise append a new evidence note without an early re-probe"追加本轮 note 后即停，不早复探。
- blocker (high): :3080 无监听者 -> dsh web host 未运行；section 4 不可执行。
- 下次动作（事件驱动）: 先 Get-NetTCPConnection -LocalPort 3080 -State Listen；有监听者->重跑 probe；无监听者->不重跑、不新增 evidence。
- 证据: .codex/patrol/openeyes-2026-09-19-round-414-gate-readiness.md（本地仅参考，git 忽略）。