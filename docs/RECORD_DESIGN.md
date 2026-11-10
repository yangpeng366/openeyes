# OpenEyes Record - Spike

**Goal**: 让不支持图片（vision）的纯文本大模型也能驱动桌面 GUI 工作流。

**两条路径都保留**：

| 路径 | 范式参考 | 何时用 |
|---|---|---|
| **show, don''t prompt** | khvni/monkeybot, OS-Atlas PC-Agent-E | 用户能现场操作一遍 |
| **prompt, don''t show** | 腾讯 WorkBuddy「实践八」 | 用户描述需求就行 |

两条路径都产出**完全相同的 Trajectory JSONL**，下游 5 种 export / replay / DAG 全部复用。

## 包结构

```
openeyes_record/
├── __init__.py        # public API
├── event.py           # UIAEvent / InputEvent / Trajectory + JSONL I/O
├── recorder.py        # show 路径：Recorder.record_click / record_type / record_hotkey
├── replayer.py        # Replayer(dry_run=True)：把 Trajectory 翻译成 OpenEyes 调用计划
├── abstract.py        # merge_trajectories / to_natural_language / graph_to_natural_language
├── generator.py       # prompt 路径：generate_trajectory_from_prompt (real + mock backend)
├── prompt_template.py # SYSTEM + USER prompt 模板
└── export/
    ├── codex_skill.py     # 输出 Codex SKILL.md
    ├── action_receipt.py  # 输出 ai-action-trace-receipt-standard YAML
    └── opencua.py         # 输出 OpenCUA AgentNet 兼容 JSONL
examples/
├── demo_mock.py       # show 路径：FakeElement 跑完整链路
├── demo_prompt.py     # prompt 路径：自然语言 → Trajectory
└── demo_real.py       # show 路径 + 真实 OpenEyes + 真实 Edge 窗口
```

## 路径 A：show, don''t prompt

```python
from openeyes import find_window, detect_elements, click_by_selector
from openeyes_record import Recorder

rec = Recorder()
rec.start(name="rotate-anyvpn-server", app_name="AnyVPN")
wins = find_window(title_contains="AnyVPN")
hwnd = wins[0].hwnd

elems = detect_elements(hwnd, restore=True)
toggle = next(e for e in elems if e.automation_id == "ServerListToggle")
click_by_selector(hwnd, "automation_id=ServerListToggle", dry_run=False)
rec.record_click(hwnd, toggle, intent="Open the server list")
# ... 继续你的操作 ...
traj = rec.stop()
traj.save("trajectory.jsonl")
```

## 路径 B：prompt, don''t show

```python
from openeyes_record import generate_trajectory_from_prompt

traj = generate_trajectory_from_prompt(
    "Rotate AnyVPN to Hong Kong and reconnect.",
    app_name="AnyVPN",
    element_inventory=[...],   # 来自 openeyes.detect_elements 的快照
    backend="auto",             # auto = 真 LLM（有 key）或 mock（无 key）
    # backend="jev",            # TypeSafe JEV 选择 inventory；无 key 默认 FakeJev
)
traj.save("trajectory.jsonl")
```

`backend` 四个值：
- `"auto"`：有 `LLM_API_KEY` 就走真 LLM，没有就 fallback 到 mock
- `"real"`：强制真 LLM，没 key 抛 `LLMUnavailable`
- `"mock"`：永远用确定性模板
- `"jev"`：用 JEV 从 inventory 选择一个可执行目标；`TYPESAFE_API_KEY`
  存在时走 `/v1/systemone` 的 `choice` 协议，否则走 `FakeJevScorer`。
  置信度沿用 ACH FEAT-03：`>=0.85` ACT、`0.60~0.85` 标记
  `requires_review`、低于下限或落入 `0.30~0.70` 不确定带则返回空轨迹。

## 路径 C：show + prompt 混合（已实现 hybrid_generate）

- 用户操作一遍 → record 出 Trajectory
- `hybrid_generate(recorded, description)` 已实现：recorded trajectory 转自然语言 → LLM 合成增强轨迹；无 key fallback 到确定性 pass-through
- 支持 `few_shot=[traj1, traj2, ...]` 作为 LLM 上下文

这正是 PC-Agent-E 论文的核心配方：312 条人工轨迹 + Claude 3.7 合成 → 训出超 Claude 的 agent。

## 共享的下游

不管哪条路径产出 Trajectory，下游完全一致：

```python
from openeyes_record import Replayer, merge_trajectories, to_natural_language
from openeyes_record.export import (
    to_codex_skill, to_action_receipt, to_opencua_trajectory,
)

# dry-run 回放（不真发输入）
Replayer(dry_run=True).replay(traj).to_dict()

# 真实回放（真发输入 + 逐步骤校验）
Replayer(dry_run=False).replay(traj).to_dict()

# 多个 variant → DAG
graph = merge_trajectories([traj1, traj2, traj3])
graph.to_dot()                      # Graphviz 可视化
to_natural_language(traj)          # 纯文本 LLM 可读

# 五种格式输出
to_codex_skill(traj, description="...")            # SKILL.md
to_action_receipt(traj, human_prompt="...")        # ai-action-trace YAML
to_opencua_trajectory(traj)                        # OpenCUA AgentNet JSONL
traj.save("trajectory.jsonl")                      # 原生 JSONL
graph.to_dot()                                      # DAG DOT
```

## 回放验证：三态与 `--live`

回放有两档，区别只在**是否把输入真的发到桌面**：

| 方式 | 行为 | 每步 `status` | 副作用 |
|---|---|---|---|
| `eyes record replay <t.jsonl>`（默认） | 只生成 `actuate()` 计划 | `planned` | 无：不读桌面、不发输入、不校验 |
| `eyes record replay <t.jsonl> --live` | 真实发送输入并轮询期望 | `verified` / `unverified` / `failed` | 会驱动本机键鼠，会改变前台窗口内容 |
| MCP `record_replay`（`live` 缺省 `false`） | 同上两档，返回 `report.to_dict()` | 同上 | 只有 `live=true` 才真实执行 |
| `Replayer(dry_run=False)`（Python API） | 真实发送 + 校验 | 同上 | 同上 |

### 三态语义

- `verified`：轨迹里记录了可判定的期望（`meta.expect` / `meta.verify` /
  `meta.expect_window_title`），且该期望在动作前**不成立**、动作后**被观察到成立**。
- `unverified`：动作已投递，但没有可判定期望；或期望的读取设备不可用（
  `hwnd=0`、无元素过滤条件）；或期望在动作前**已经成立**——此时事后看到它成立
  不能证明是本动作造成的。
- `failed`：动作在超时内没能让期望成立，或根本没有可投递的路径
  （既无结构化 selector 又无 bbox 中心）、或投递本身抛错。

`skipped` 是计划阶段就识别出的未知动作，单独计数，不进入上面三态。

### 「已发送」不等于「已生效」

报告里每步有 `sent` 字段，它只表示**输入已投递**，不表示界面状态发生了改变：

- 投递成功但期望未成立 → `failed`，`sent` 仍为 `true`；
- 投递成功且没有期望可判 → `unverified`，`sent` 为 `true`；
- 期望在动作前就已成立 → `unverified`，`sent` 为 `true`（`preexisting=true`）。

因此 `summary.all_effect_verified` 只在「至少一个 verified，且 failed 与
unverified 都为 0」时为 `true`；dry-run 下该字段为 `null`（没有任何结论）。
录制器当前默认不写 `meta.expect`，所以录下来的轨迹直接 `--live` 回放通常是
`unverified`——要拿到 `verified`，录制时传入 `meta={"expect": {...}}`。

期望只在机器可复核时才成立：`window_title` / `app_name` / `pid` / `hwnd` 是
采集时上下文（回放开始时窗口通常已经在，标题「还在」证明不了任何事），
`intent` 是自然语言，`input_text` 只是按键内容（掩码框、焦点被抢、输入法都会
让「文本已输入」失真），这些都不作为期望。

### CLI 退出码与输出

- 无 `failed` → `0`；有 `failed` → `1`（stderr 另打一行 `error: N of M steps failed`）；
  参数/文件错误仍由 click 返回 `2`。
- `--json` 在两种模式下都只把 `report.to_dict()` 打到 stdout，便于管道解析。
- 不带 `--json`：dry-run 保持原有计划输出；`--live` 逐步打印
  `index: status reason`，末行 `N verified / N unverified / N failed`。

### MCP

`record_replay(trajectory_path, live=false, retries=2, timeout=2.0, interval=0.1)`
返回 `report.to_dict()` 外加 `ok`（无 failed 即为 `true`）与 `failed_steps`
（每个失败步骤的 `index` / `action` / `reason` / `error`）。`actions[]` 原样保留，
失败明细不会被摘要覆盖。参数错误与读不到文件时返回 `{"error": ..., "tool": "record_replay"}`。

## 降低门槛的设计要点（适用于所有路径）

1. **不录截图**：Element schema 完全结构化（name / automation_id / control_type / hint / parent_chain / state），没有 pixel。
2. **可文本消费**：所有产物（trajectory JSONL / SKILL.md / action_trace YAML）都是纯文本，**纯文本 LLM 也能读懂、也能写出新轨迹**。
3. **回放不需要 vision**：Replayer 用 `click_by_selector(automation_id=...)` 直接命中 UIA 元素，不截图识别坐标。
4. **多源兼容**：同一个 trajectory 可以输出 Codex skill / ai-action-trace / OpenCUA 三种下游格式。
5. **DAG 抽象自动去重**：录同一任务 3 次（不同元素名 / 不同 hotkey），`merge_trajectories` 自动生成 happy path + 备选分支。

## 已参考的开源项目

| 项目 | 用法 |
|---|---|
| `khvni/monkeybot` (★0) | show 路径范式，「show, don''t prompt」 |
| 腾讯 WorkBuddy「实践八」| prompt 路径范式，「prompt, don''t show」 |
| `xlang-ai/OpenCUA` (★833) | AgentNet 数据集 + AgentNetTool 标注工具 |
| `xlang-ai/PC-Agent-E` | 312 轨迹 + AI 增强（arxiv:2505.13909） |
| `SamuraiWriter7/ai-action-trace-receipt-standard` (v0.5.0) | AI Action Trace Receipt 开放标准 |

## Spike 已验证

| Demo | 路径 | 状态 |
|---|---|---|
| `python examples/demo_mock.py` | show (FakeElement) | 跑通：7 步 / DAG 10 nodes / 5 种格式 |
| `python examples/demo_prompt.py` | prompt (mock backend) | 跑通：2 个 trajectory / DAG 8 nodes / 3 种格式 |
| `python examples/demo_real.py Edge` | show (真实 OpenEyes) | 跑通：真实 Edge 50 元素 / 5 步 trajectory |
| `python examples/record_demo_real.py Edge` + chrome bridge probe | 双路径 schema | 2026-09-22：真实 Edge 428 元素 / 5 步；bridge 语义合并后 Python `Trajectory.load` 与 JS `parseJSONL` 双向读取 5 步 |
| examples/record_edge_demo.js | 双路径 schema | 2026-09-22 Round 595：可复现 headless Edge 录制 example；5 个动作语义合并为 5 步，Python/JS 双向验证通过；无绝对路径依赖 |
| `LLM_API_KEY=... python examples/demo_prompt.py --real` | prompt (real backend) | 待用户配置 key 后跑 |
| `examples/verify_record_schema.py` | dual-source contract | Round 598: Python round-trip, Node bridge reparse, and JSON evidence report |

输出在 `examples/out/`：
- `trajectory.jsonl` (3.2 KB) — show mock 主产物
- `SKILL.md` (1.6 KB) — Codex skill
- `action_trace.yaml` (6.4 KB) — ai-action-trace
- `opencua.jsonl` (3.7 KB) — OpenCUA AgentNet
- `trajectory.dot` (2.1 KB) — DAG 可视化
- `trajectory_real.jsonl` (3.8 KB) — show 真实
- `SKILL_real.md` (1.7 KB) — 真实 SKILL
- `prompt_skill.md` (1.5 KB) — prompt 路径产物
- `prompt_action_trace.yaml` (6.3 KB)
- `prompt_opencua.jsonl` (3.6 KB)
