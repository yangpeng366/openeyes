## Round 362 — 2026-09-18T18:21:31+08:00 — gate-readiness probe (auto-patrol NEW round-362)
- 命令: python examples\dsh-gate-readiness.py（使用脚本默认 --timeout 2.0 与默认 --next-recheck 2026-09-10T16:30:00+08:00）
- 退出码: 2（脚本内部 sys.exit(2) 标记非 ready；PowerShell 包裹层 echo EXIT=2）
- 结果:
  ```json
  {"gate_url": "http://127.0.0.1:3080/", "ready": false, "status_code": null, "error": "<urlopen error timed out>", "next_recheck": "2026-09-10T16:30:00+08:00", "next_action": "Wait for the dsh web host to return HTTP 200, then rerun this probe."}
  ```
- 处置: 不启 dsh、不跑 docs/dsh-web-acceptance.md section 4、不发 browser_click；本轮按 NEW 触发条件仅一次只读探测即止，不做早复探。下一轮 planner 具体看点：到既有 FIXED 复探点 2026-09-30T12:00:00+08:00 后再重跑 readiness probe；本轮不携入工作区既有业务改动。
- blocker (high): http://127.0.0.1:3080/ 请求超时，无法确认 HTTP 200；等待 dsh web host 恢复后再按 gate 结果决定是否执行 section 4。
- 证据: .codex/patrol/openeyes-2026-09-18-round-362-gate-readiness.md（本地仅参考，git 忽略）。