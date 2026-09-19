# Round 419 last-patrol handoff (mirror)

- 命令: python examples\dsh-gate-readiness.py
- 退出码: 2（ready=false；持续 timeout）
- 结果: {"gate_url": "http://127.0.0.1:3080/", "ready": false, "status_code": null, "error": "<urlopen error timed out>", "next_recheck": "2026-09-10T16:30:00+08:00", "next_action": "Wait for the dsh web host to return HTTP 200, then rerun this probe."}
- 诊断: Get-NetTCPConnection -LocalPort 3080 -State Listen -> 0；Get-Process -Name dsh -> 0；Get-Command dsh -> E:\npm-global\dsh.ps1（dsh 已安装，仅未启动）。根因未变（round-382）: dsh web host 未运行 + HNS/虚拟网络层静默丢弃 :3080 loopback SYN（FW 已关、无 portproxy、无代理）。
- 时间门: 方向表要求"On or after 2026-09-11T10:00:00+08:00"重跑；今 2026-09-19T15:25:14+08:00，门已过，本轮按要求强制跑一次 probe（2s timeout，HTTP 仍超时），结论同前。
- 处置: 按方向表"otherwise append a new evidence note without an early re-probe"追加本轮 note 后即停，不早复探。不启 dsh（持久 web host 超单轮预算且违反不留后台进程）、不跑 section 4、不发 browser_click。record/ 业务改动与既有 M 文件维持原样未动。
- blocker (high): :3080 无监听者 -> dsh web host 未运行；section 4 不可执行。用户侧待办: (a) 启动 dsh web host 绑定 :3080（dsh 已装于 E:\npm-global\dsh.ps1）/ (b) wsl --shutdown 清陈旧 HNS / (c) 排查 EDR 拦截 :3080。
- 下次动作（事件驱动）: 先 Get-NetTCPConnection -LocalPort 3080 -State Listen；有监听者->重跑 probe，ready=true 执行 docs/dsh-web-acceptance.md section 4 两 tab 点击 dry-run；无监听者->不重跑、不新增 evidence。强烈建议用户更新项目方向表下一步为"当 :3080 出现监听者后重跑 probe"以止每轮定时复探空转（已连 419 轮同结论）。
- 证据: .codex/patrol/openeyes-2026-09-19-round-419-gate-readiness.md（本地仅参考，git 忽略）。