## Round 605 - 2026-10-08
- 方向：续推 Round 604 后续；把 dsh gate 由固定间隔复探切到事件触发。
- 落地：docs/debug-experience.md 新增 Round 605 event-driven dsh gate 段；刷新根与 .codex 跟踪文件。
- 状态：lastExit=2, consecutiveFailures=2, resumeCount=4；DSH 仍超时，listener_count=0，dsh_count=0。
- 本轮不复探 readiness probe，避免再产生同质 evidence。
- ahead origin 49，仍待用户授权按 5 主题 squash 推送。

## 下一轮要看的具体点
- 用户授权推送 feat/decision-jev-layer 时按 5 主题 squash 推 origin。
- 真实 TYPESAFE_API_KEY 配齐后手工 smoke hybrid_generate 真实路径。
- 若 dsh 服务在监听恢复 / 用户显式确认后，重跑 readiness probe 并进入 Section 4。
- 评估把 OpenEyes 项目在 Start-CodexAutoPatrolLoop.ps1 标记为 dshGateMode=event-driven，避免连续 exit 2。

## blocker
- high：dsh web host 仍不可达；event-driven 等待事件。
- low：TYPESAFE_API_KEY 未配置，真实 LLM transport 无法实测。