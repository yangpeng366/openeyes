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

## 路径 C：show + prompt 混合（下一步要做）

- 用户操作一遍 → record 出 Trajectory
- LLM 看 trajectory + 用户自然语言补充 → 生成更高质量 skill
- 也可让 LLM 基于已有 trajectory 库作为 few-shot 合成新轨迹

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
| `python examples/record_demo_real.py Edge` + chrome bridge probe | 双路径 schema | 2026-09-22：真实 Edge 428 元素 / 5 步；Python `Trajectory.load` 可读 bridge 13 步，JS `parseJSONL` 可读 native 5 步 |
| temporary headless Edge bridge runner | 双路径 schema | 2026-09-22 Round 593：真实 Edge 5 步 artifact `examples/record-out/trajectory-edge-real.jsonl`；与 UIA 基线同为 5 步，Python load + JSONL reparse 双向一致 |
| `LLM_API_KEY=... python examples/demo_prompt.py --real` | prompt (real backend) | 待用户配置 key 后跑 |

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
