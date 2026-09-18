## Round 348 — 2026-09-18T12:44:56+08:00 — gate-readiness probe (auto-patrol NEW round-348)
- 命令: python examples\dsh-gate-readiness.py
- 退出码: 2（脚本内部 sys.exit(2) 标记非 ready；PowerShell 包裹层 echo EXIT_CODE=2）
- 结果:
  `json
  {"gate_url": "http://127.0.0.1:3080/", "ready": false, "status_code": null, "error": "<urlopen error timed out>", "next_recheck": "2026-09-10T16:30:00+08:00", "next_action": "Wait for the dsh web host to return HTTP 200, then rerun this probe."}
  `
- 处置: 不启 dsh、不执行 docs/dsh-web-acceptance.md section 4、不发 browser_click；按 NEW 触发条件仅完成一次只读探测，不做早复探。工作区既有未提交改动维持原样，本轮不携入。
- blocker (high): http://127.0.0.1:3080/ 请求超时，无法确认 HTTP 200；沿用既有 fixed 复探时间 2026-09-30T12:00:00+08:00，届时再按 gate 结果决定是否执行 section 4。
- 证据: .codex/patrol/openeyes-2026-09-18-round-348-gate-readiness.md。