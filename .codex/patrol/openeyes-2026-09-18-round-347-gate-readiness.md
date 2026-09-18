## Round 347 — 2026-09-18T12:22:06+08:00 — gate-readiness probe (auto-patrol NEW round-347)
- 命令: python examples\dsh-gate-readiness.py --next-recheck 2026-09-30T12:00:00+08:00（沿用脚本默认 --timeout 2.0）
- 退出码: 2（脚本内部 sys.exit(2) 标记非 ready；PowerShell 包裹层 echo EXIT=2）
- 结果:
  `json
  {"gate_url": "http://127.0.0.1:3080/", "ready": false, "status_code": null, "error": "<urlopen error timed out>", "next_recheck": "2026-09-30T12:00:00+08:00", "next_action": "Wait for the dsh web host to return HTTP 200, then rerun this probe."}
  `
- 处置: 不启 dsh、不跑 docs/dsh-web-acceptance.md section 4、不发 browser_click；本轮按 NEW 触发条件仅一次只读探测即止，不做早复探。工作区既有未提交改动维持原样，本轮不携入。
- blocker (high): http://127.0.0.1:3080/ 请求超时，无法确认 HTTP 200；下次复探固定为 2026-09-30T12:00:00+08:00，届时再按 gate 结果决定是否执行 section 4。
- 证据: .codex/patrol/openeyes-2026-09-18-round-347-gate-readiness.md。