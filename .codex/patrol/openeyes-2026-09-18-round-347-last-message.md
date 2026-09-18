# Round 347 last-message handoff

- 命令: python examples\dsh-gate-readiness.py --next-recheck 2026-09-30T12:00:00+08:00
- 退出码: 2
- 结果: ready=false, status_code=null, error='<urlopen error timed out>', next_recheck=2026-09-30T12:00:00+08:00, next_action='Wait for the dsh web host to return HTTP 200, then rerun this probe.'
- 处置: 仅一次只读探测；不启 dsh、不跑 section 4、不发 browser_click；不早复探。
- 下次复探: 2026-09-30T12:00:00+08:00（FIXED，沿用 round-291）。
- 工作区未提交改动维持原样，本轮仅新增两份巡检记录，不携入业务改动。
- blocker (high): http://127.0.0.1:3080/ 请求超时，等待 dsh web host 恢复 HTTP 200。