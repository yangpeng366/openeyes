# Round 367 — 2026-09-18T20:40:35+08:00 — gate-readiness probe (auto-patrol NEW round-367)

- 命令: python examples\dsh-gate-readiness.py（默认 --timeout 2.0；未传 --next-recheck，沿用脚本内置 2026-09-10T16:30:00+08:00 字段，但本轮仍按既有 FIXED 复探点 2026-09-30T12:00:00+08:00 执行）
- 退出码: 2（脚本内部 sys.exit(2) 标记非 ready）
- 结果:
  ```json
  {"gate_url":"http://127.0.0.1:3080/","ready":false,"status_code":null,"error":"<urlopen error timed out>","next_recheck":"2026-09-10T16:30:00+08:00","next_action":"Wait for the dsh web host to return HTTP 200, then rerun this probe."}
  ```
- 处置: 仅一次只读探测；不启 dsh、不跑 docs/dsh-web-acceptance.md section 4、不发 browser_click；不早复探。下一轮 planner 到 FIXED 复探点 2026-09-30T12:00:00+08:00 后再重跑 readiness probe；本轮不携入工作区既有业务改动（CHANGELOG.md / README.md / openeyes/__init__.py / pyproject.toml / tests/test_smoke.py 的未提交 M 与 record/ 新模块维持原样）。
- 工作区未提交业务改动维持原样，本轮仅新增巡检记录，未触碰业务代码。
- 下次动作: 到 FIXED 复探点 2026-09-30T12:00:00+08:00 后重跑 readiness probe；若 HTTP 200，执行 docs/dsh-web-acceptance.md section 4 两 tab 点击 dry-run。
- blocker (high): http://127.0.0.1:3080/ 请求超时（<urlopen error timed out>），未确认 HTTP 200；继续等待 dsh web host 恢复。