# AGENTS.md — OpenEyes 交接要点

面向接手的 agent / 人类。目标：读完这页就能动手改，不必修完再回头翻长文。

## 这项目是干什么的

AI 友好的 Windows 桌面操作原语：`see → resolve → act`。
任何大模型 agent 通过 CLI（`eyes`）或 MCP server 驱动桌面 GUI。
核心资产是**结构化 element schema** 与**降级链**，不是模型。

## 六条主要思路

1. **结构化优先**：`uia → cdp → vision`。能走无障碍树 / DOM 就不看像素；
   截图是最后手段，且走解析式 vision（OmniParser v2：先解析成结构化元素再决策）。
   每次结果都带 `source`，说明这答案从哪来。
2. **易点击**：UIA 命中点中心 → 未命中点 vision bbox 中心 → 都不命中走字母 hint overlay。
   任何一步都要有可解释的降级原因。
3. **默认 dry-run**：所有改变状态的操作先 dry-run，显式 `--go` 才真执行。
   验收门禁据此判定「真跑了」还是「只是演练」。
4. **MCP 优先**：23 个工具的契约是一份稳定接口，改内部不许改契约；
   `examples/mcp-stdio-probe.py` 就是为了守住它。
5. **Record & Replay 无需 vision**：录的是逻辑步骤（UIAEvent）而非原始事件流，
   回放翻译成 `openeyes.actuate()` 调用；backend 可注入（`auto` / `jev` 真模型 / `mock`），
   真模型缺凭据时回落 mock，不静默卡住。
6. **判断交给模型，阈值留在代码**：`openeyes/decision/` 的 `decide()` 把打分分四带
   （ACT / FALLBACK / UNCERTAIN / FLAG_FOR_REVIEW），只有 FLAG_FOR_REVIEW 这一带
   会置 `needs_attention=True`——人的注意力只花在真需要人看的地方。
7. **验收要能判红**：浏览器四路探针（type / shot / 双跑 / 失败面）统一一套 verdict 模型，
   双跑比对要真哈希；判据必须「有可能失败」，否则门禁形同虚设。

## 改动规矩

- 跑 `pwsh -NoProfile -File examples\quality-gate.ps1`：ruff、UTF-8 无 BOM、MCP 契约 23 工具、pytest 全套。
  CI 跑的是同一串步骤，别只跑局部。
- 全量测试目前 1185 passed / 7 skipped；改到公共路径就把全量跑一遍。
- 真机验收用可见窗口（真实 Edge profile），不用无头。
- 源文件 UTF-8 无 BOM（`examples/bom-sweep.py` 会扫）。
- 提交直接推 `origin main`（单人维护，不走 PR、不 force push）。

## 接口速查（agent 接入只看这一层）

- CLI：`eyes <command> --json`，字段固定；子命令先 dry-run 再 `--go`。
- MCP：`openeyes-mcp`，工具名与 CLI 一一对应。
- 退出码：0 成功 / 1 失败 / 2 参数错 / 3 没匹配到目标 / 4 后端不可达 / 5 动作没生效。
  JSON 走 stdout，人读错误走 stderr。
- 元素字段：`bbox / center / control_type / name / automation_id / class / state`。
- 要更深：README 的 Architecture 与 Record & Replay 两节。

## 已知边界

- 只有 Windows 的 UIA 后端真正可用；macOS / Linux 在路线图上。
- 远程浏览器走 SSH 端口转发 + Host 头改写，隧道断了就是断了，不自动修。
- vision backend 依赖外部模型权重，离线环境只到 uia / cdp 这层。

## 实践与调试

> 每条都是实测过的坑：症状 → 原因 → 对策。命令照仓库实情写，先说「跑什么、看什么」。

**1. 中断与回执（四路验收探针）**：Ctrl+Break → exit 130 + verdict=INTERRUPTED，且 `--out`
目录必须为空（不写回执）；`taskkill` 走 Windows `STATUS_CONTROL_C_EXIT`（0xC000013A）；
`TerminateProcess` 硬杀 → exit 1 且无回执——**只存在 PNG 不算通过**，回执是唯一凭证。
对策：探针一律用空的 `--out` 起步，跑完先看回执再看产物。

**2. 控制台事件只打死 pwsh 的坑**：ps 腿退出码曾被误记成 `powershell-exit-3221225786` 并写出了
回执（把中断当成了命令失败）。现在 `_acceptance_verdict.py` 识别 `STATUS_CONTROL_C_EXIT`
（3221225786 / -1073741510）并走中断分支：删 `.tmp`/`.part`、exit 130、不写回执。

**3. 清理要查干净**：msedge 进程数（区分自己开的 `--remote-debugging-port`）、目标端口是否还
LISTEN、fixture server 是否已停、临时 profile 是否删除。实测提醒：`netstat` 曾只显示
TIME_WAIT 而端口实际在听——用 pwsh 打一次 `http://127.0.0.1:9222/json/version` 验可达性更可靠。

**4. 降级链 uia → cdp → vision**：每一步都要有可解释的原因，结果里带 `source`；结构化优先，
能走无障碍树 / DOM 就不看像素。排查时先看 `source` 字段是谁给的，再决定往哪层查。

**5. 远程浏览器隧道**：hosts 同名双映射时 Windows 只取第一条匹配；hosts 指向 `127.0.0.1` 会
自环——解析结果必须滤掉 loopback 与私网；隧道断了就是断了，不自动修，重连要人工介入。

**6. 验收要能判红**：浏览器四路探针（type / shot / scan / click）共用
`examples/_acceptance_verdict.py` 一套 verdict 模型（FAIL > SKIP > PASS），双跑比对用真哈希
（`judgement_sha256`）。判据必须「有可能失败」——写死判据等于没验。实测：本机 Edge CDP 在听
（ps 腿 200 PASS），但 page 腿 FAIL/`page-unexpected`/error 空——MCP server 子进程缺
`websocket-client` 起不来，整体 FAIL exit 2，这是探针在环境不完整时的正确判红。

**7. 退出码语义（`eyes` CLI）**：0 成功 / 1 失败 / 2 参数错 / 3 没匹配到目标 / 4 后端不可达 /
5 动作没生效。JSON 走 stdout，人读错误走 stderr。（探针自己另有一套：0 PASS / 2 FAIL /
3 SKIP / 130 INTERRUPTED。）

**8. 浏览器调试用可见窗口**：不用无头模式（真实 Edge profile）——headless 下 UIA / 截图行为
与真机不一致，验收结果不可信。

**9. 场景探针（一帧多判）**：问「运行中还是空闲」时，别逐个 `anchor find`——每次 find 都是一次
独立截图，UI 一动，两次结论就可能互相矛盾。改用 `eyes anchor probe --names a,b,c`：一帧、
N 个模板、一份回执（`matched` / `ambiguous` / `missing` / `unreadable` + `frame.sha256`）。
`--roi-pad N` 只在模板存档矩形附近搜：Trae 实测（1886x1183 @1.3021）三个锚点整窗约 2.9 s，
`--roi-pad 48` 约 0.12 s；代价是控件挪动超过 pad 就读成 `missing`，ROI 外的同像素孪生体
不再造成假 `ambiguous`。最小化窗口的截图是 207x35 的占位条，早期会把所有锚点误报成 `missing`，
现由探针直接回 `WINDOW_MINIMIZED`；恢复用 `eyes focus --window H --restore --go`（ShowWindow
(SW_RESTORE) 后重新读窗口身份再聚焦）。MCP 侧同名只读工具：`anchor_probe` / `anchor_wait`。

**10. 截图抓的是「屏幕上的那块」，不是「那个窗口」（2026-11-14 实测）**：`ImageGrab.grab(bbox=...)` 抓的是
屏幕该矩形处的东西，被别的窗口盖住时，OCR 读回来的是**盖在上面的那个窗口**（实测在 Trae 窗口
矩形上读到的是 Codex 自己的对话）。治法：`PrintWindow` + `PW_RENDERFULLCONTENT`（2）请窗口自画，
Electron / Chromium 必须带 flag 2；实测 Trae CN 在后台也能读出真正的 SOLO 页面。失败时退回屏幕抓取，
并用 `capture.method` 注明走的哪条路。

**本地自测怎么跑**（在仓库根，pwsh 7）：

```powershell
pwsh -NoProfile -File examples\quality-gate.ps1            # 全量：ruff + bom-sweep + MCP 契约 + pytest（1185 passed / 7 skipped）
pwsh -NoProfile -File examples\quality-gate.ps1 -SkipTests  # 只跑快的三项（不跑 pytest）
python -m ruff check .
python examples\bom-sweep.py
python examples\mcp-stdio-probe.py
python -m pytest tests/ -q
python examples\browser-click-acceptance.py --out <空目录>    # 验收探针，见第 1/6 条
```

本机实测：Loomy 自带 python 3.13.13 缺 pytest / ruff / `websocket-client`，quality-gate 会在
ruff 与 mcp-stdio-probe 两步报 `ModuleNotFoundError`（bom-sweep 通过，344 个文件无 BOM）。先
`python -m pip install -e .[dev]`（或至少 `ruff websocket-client pytest pytest-asyncio`）再跑；
装完用 mcp-stdio-probe 验证 MCP server 子进程能起来，再跑探针。
