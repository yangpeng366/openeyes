## Round 360 — 2026-09-18T17:41:23+08:00 — gate-readiness probe (auto-patrol NEW round-360)

- 命令: python examples\dsh-gate-readiness.py --next-recheck 2026-09-30T12:00:00+08:00（沿用脚本默认 --timeout 2.0；显式 --next-recheck 与 round-345 / round-352..359 设定的 FIXED recheck 对齐）
- 退出码: 2（脚本内部 sys.exit(2) 标记非 ready；PowerShell 包裹层 echo EXIT=2）
- 结果:
  `json
  {"gate_url": "http://127.0.0.1:3080/", "ready": false, "status_code": null, "error": "<urlopen error timed out>", "next_recheck": "2026-09-30T12:00:00+08:00", "next_action": "Wait for the dsh web host to return HTTP 200, then rerun this probe."}
  `
- 处置: 沿用 round-231..359 结论；不启 dsh、不跑 section 4、不发 browser_click；本轮按 NEW 触发条件仅一次只读探测即止，不做早复探；下次复探维持 round-345 / round-352..359 设定的 FIXED 2026-09-30T12:00:00+08:00。本轮 commit 仅含 round-360 单一证据（round-359 已提交于 da6d6ea，无积压）。工作区未提交业务改动（CHANGELOG.md / README.md / openeyes/__init__.py / pyproject.toml / tests/test_smoke.py 修订 + docs/RECORD_DESIGN.md / openeyes/record/ / tests/test_record.py / examples/record_*.py / examples/out/ / examples/record-out/ 新增）维持「与本轮 gate-readiness 无关、本轮不携入」，留待后续人工评审后再处理。
- blocker (high): http://127.0.0.1:3080/ 请求超时，无法确认 HTTP 200；等待 dsh web host 恢复后再按 gate 结果决定是否执行 section 4。
- 证据: .codex/patrol/openeyes-2026-09-18-round-360-gate-readiness.md（本地仅参考，git 忽略）。