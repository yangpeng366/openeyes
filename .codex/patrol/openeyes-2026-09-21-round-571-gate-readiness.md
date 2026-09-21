# Round 571 gate-readiness - 2026-09-21T20:02:24+08:00

- 状态：dsh web gate 仍不可达；:3080 listener_count=0、dsh_count=0。
- 命令：Get-NetTCPConnection -LocalPort 3080 -State Listen；Get-Process -Name dsh。
- 探针：python examples/dsh-gate-readiness.py 本轮未重跑（沿用 round-527 起事件驱动旁证建议）。
- 处置：docs/dsh-web-acceptance.md 追加 Round 571 evidence；刷新 last、.codex/last-message.md、.codex/last-round.md；本地 commit（[auto-patrol]），不 push。
- blocker：high（:3080 无监听者，需服务负责人恢复 dsh web host）；medium（同质 evidence 516-571 连续 56 轮）。
- 下一轮：仅当 :3080 出现监听者才重跑 readiness probe；否则保持事件驱动旁证 + 留证刷新。