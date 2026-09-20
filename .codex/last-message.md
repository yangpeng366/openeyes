## 本轮推进
- Round 517，2026-09-21T02:01:47+08:00；仅探测一次，验收未推进，原因是 gate 请求超时。
- 命令：python examples\dsh-gate-readiness.py
- 输出：ready=false，status_code=null，error=<urlopen error timed out>；exit 2。
- 旁证：沿用 round-446 结论（dsh 已装未启 / :3080 无监听者 / HNS+EDR 阻断），本轮未复跑 Get-NetTCPConnection / Get-Process / Get-Command，节 token。
- 已追加 .codex/patrol/openeyes-2026-09-21-round-450-gate-readiness.md + docs/dsh-web-acceptance.md ## Round 517 note；未启动服务、未执行 section 4、未早期复探；既有业务改动保留，不提交 main、不创建分支。

## 下一轮要看的具体点
- 服务负责人确认恢复后运行 readiness probe；仅 HTTP 200 时执行 docs/dsh-web-acceptance.md section 4。
- 建议 planner 改为服务恢复后触发；脚本 next_recheck=2026-09-10T16:30:00+08:00 是过期默认值，不据此立即复探。
- 事件驱动复探更经济：先 Get-NetTCPConnection -LocalPort 3080 -State Listen；有监听者 -> 重跑 probe；无监听者 -> 仅刷新 last-message.md（不再每轮追加 runbook note，以止 450 轮同结论空转）。

## blocker
- high：gate 未就绪，需服务负责人恢复 :3080 HTTP 服务；本轮未查明 HNS 或 EDR 根因。
- medium：runbook 同质 evidence 已连 450 轮，建议改方向表为事件驱动复探，或授权归档历史 Round note。