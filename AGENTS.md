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
4. **MCP 优先**：19 个工具的契约是一份稳定接口，改内部不许改契约；
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

- 跑 `pwsh -NoProfile -File examples\quality-gate.ps1`：ruff、UTF-8 无 BOM、MCP 契约 19 工具、pytest 全套。
  CI 跑的是同一串步骤，别只跑局部。
- 全量测试目前 1022 passed / 7 skipped；改到公共路径就把全量跑一遍。
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
