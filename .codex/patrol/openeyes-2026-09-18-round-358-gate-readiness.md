## Round 358 — 2026-09-18T17:01:35+08:00 — gate-readiness probe (auto-patrol NEW round-358)

- 命令: python examples\dsh-gate-readiness.py --next-recheck 2026-09-30T12:00:00+08:00（沿用脚本默认 --timeout 2.0；显式 --next-recheck 与 round-345 / round-352..357 设定的 FIXED recheck 对齐）
- 退出码: 2（脚本内部 sys.exit(2) 标记非 ready；PowerShell 包裹层 echo EXIT=2）
- 结果:
  `json
  {"gate_url": "http://127.0.0.1:3080/", "ready": false, "status_code": null, "error": "<urlopen error timed out>", "next_recheck": "2026-09-30T12:00:00+08:00", "next_action": "Wait for the dsh web host to return HTTP 200, then rerun this probe."}
  `
- 处置: 沿用 round-231..357 结论；不启 dsh、不跑 section 4、不发 browser_click；本轮按 NEW 触发条件仅一次只读探测即止，不做早复探；下次复探维持 round-345 / round-352..357 设定的 FIXED 2026-09-30T12:00:00+08:00。本轮 commit 仅含 round-358 单一证据（round-357 已提交于 7c25e44，无积压）。工作区未提交业务改动（CHANGELOG.md / README.md / openeyes/__init__.py / pyproject.toml / tests/test_smoke.py 修订 + docs/RECORD_DESIGN.md / openeyes/record/ / tests/test_record.py / examples/record_*.py / examples/out/ / examples/record-out/ 新增）维持「与本轮 gate-readiness 无关、本轮不携入」，留待后续人工评审后再处理。
- blocker: http://127.0.0.1:3080/ 持续不可连接（与 round-249 / round-251 / round-254..258 / round-264..272 / round-273..357 timed out 互见，源相同：3080 端口监听缺位 + AnyVPN 路由抖动）。建议：复探间隔 >= 7 天，避免短期内重复触发只读探测。
- 证据: .codex/patrol/openeyes-2026-09-18-round-358-gate-readiness.md。