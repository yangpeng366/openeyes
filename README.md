# OpenEyes（开源点睛）

> **AI-friendly computer-use primitives for Windows desktops.**
> Capture. Detect. Click. Let any LLM agent drive any desktop GUI.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Windows](https://img.shields.io/badge/platform-windows-lightgrey)](https://github.com/yangpeng366/openeyes)
[![M8ven Score](https://m8ven.ai/badge/mcp/yangpeng366-openeyes-181btc)](https://m8ven.ai/mcp/yangpeng366-openeyes-181btc)

GitHub: <https://github.com/yangpeng366/openeyes> · 中文 · [English](#english) · [Quick start](#quick-start) · [Architecture](#architecture) · [Roadmap](#roadmap) · [License](#license)

---

<a id="中文"></a>

## 中文

**OpenEyes（点睛）** 是一个开源的 AI 友好电脑使用平台。基于「see → resolve → act」三段式原语，让任何 LLM agent 都能可靠地操控桌面 GUI 应用；提供 Vimium 风格字母 hint overlay 作为人类可调试的回退通道，并通过 MCP server 暴露给所有 agent。

### 为什么做这个

| 现状 | 痛点 |
|---|---|
| Neverclick | 闭源付费，仅 Windows，无 API |
| OmniParser / OS-Atlas | 只做 detection，没有 actuation，模型重 |
| Anthropic Computer Use / OpenAI Operator | 仅 Linux VM，看不见本机，不可定制 |
| UiBot / APA | 录制型，对动态 UI 适应性差 |
| pywinauto / xdotool | 纯库，无 AI 友好的 element schema |

OpenEyes 把这些能力合成一个 MIT 开源、跨平台、AI 友好的统一平台。

### 特性

- **AI 友好 element schema**：每个 UI 元素输出结构化 JSON（bbox / center / control_type / name / automation_id / class / state）
- **易点击**：UIA 命中 → 自动点中心；不命中 → vision bbox → 点中心；都不命中 → 字母 hint overlay
- **安全**：默认 dry-run，所有状态改变操作可审计
- **MCP 优先**：所有原语通过 MCP server 暴露给任何 agent
- **结构化优先**：`uia -> cdp -> vision` 降级链，能走无障碍树 / DOM 就不看像素，截图只作最后手段
- **解析式 vision backend**：OmniParser v2 兼容，屏幕先被解析成结构化元素再决策，不让模型对着像素点坐标
- **Windows 优先**：当前只有 Windows（UIA）后端真正可用；macOS / Linux 在路线图上，尚未实现
- **远程浏览器**：经 SSH 转发驱动另一台机器上的 Firefox（WebDriver BiDi），隧道与 Host 头一并处理
- **会话恢复**：`recover_session` 把「掉线 → 拉起进程 → 重连 → 状态稳定」收进一个预算（默认 20 秒），报告带实际耗时，可断言、可 dry-run

### 设计文档

完整设计书：https://my.feishu.cn/docx/Ul6gdMULGo5VfzxDVAYcVoX6n9e （中文，含架构图 / 模块清单 / 路线图）

---

<a id="english"></a>

## English

**OpenEyes** is an open-source, AI-friendly computer-use platform. Built on a "see → resolve → act" pipeline, it lets any LLM agent reliably drive any desktop GUI. Includes a Vimium-style letter-hint overlay as a human-debuggable fallback, and exposes every primitive via MCP server for any agent.

### Why

| Today | Pain |
|---|---|
| Neverclick | Closed-source, paid, Windows-only, no API |
| OmniParser / OS-Atlas | Detection only, no actuation, heavy models |
| Anthropic Computer Use / Operator | Linux VMs only, opaque, no audit |
| UiBot / APA | Record/playback, weak on dynamic UIs |
| pywinauto / xdotool | Raw libs, no AI-friendly schema |

OpenEyes fuses these into one MIT-licensed, AI-friendly platform for Windows desktops.

### Features

- **AI-friendly element schema** — every interactive element exposes structured JSON
- **Easy click** — UIA hit → bbox center; fallback vision bbox → center; final fallback letter hint
- **Auditable degradation** — `resolve` reports which backend answered and why the chain fell through
- **Safe by default** — dry-run; full audit trail for state-changing actions
- **MCP-first** — every primitive exposed as an MCP tool to any agent
- **Structured first** — a `uia -> cdp -> vision` degradation chain: prefer the accessibility tree or the DOM, treat pixels as the last resort
- **Parsing-style vision backend** — OmniParser v2 compatible; the screen is parsed into structured elements before anything decides, never raw coordinate clicking
- **Windows first** — the Windows (pywinauto/UIA) backend is the one that works today; macOS and Linux are on the roadmap, not in the box
- **Remote browser** — drive a Firefox on another machine through an SSH forward (WebDriver BiDi); the tunnel and the Host header are handled for you
- **Session recovery** — `recover_session` chases a dropped session (detect -> start the process -> reconnect -> settle) inside one budget (20 s by default) and reports the seconds it took, so the promise can be asserted instead of hoped for

---

<a id="quick-start"></a>

## Quick start

### Windows

```powershell
git clone https://github.com/yangpeng366/openeyes.git
cd openeyes
pip install -e ".[windows,mcp]"

# 1. list visible top-level windows; take an hwnd from the table
eyes windows list

# 2. capture that window (0 = the whole screen)
eyes capture --window <hwnd> --out shot.png

# 3. enumerate interactive elements
eyes detect --window <hwnd> --pretty

# 4. click by text: dry-run first, then --go
eyes click --window <hwnd> --name-contains "Submit"
eyes click --window <hwnd> --name-contains "Submit" --go --budget 1

# 5. start MCP server (for Codex / Claude / Cursor)
eyes-mcp
```

`--budget N` allows at most N physical clicks in one run (default `0`:
semantic clicks only). A control with no `invoke` path then fails with
`BACKGROUND_UNAVAILABLE` instead of moving your mouse; add `--budget 1`,
or ask for it by name with `--method pixel`.

### Output contract

Every command answers the same way, so a script (or an agent) never has
to guess:

- `--json` is a **global** flag - it works before *or* after the
  subcommand: `eyes --json detect --window 123` and
  `eyes detect --window 123 --json` are the same call.
- The JSON goes to **stdout**; the human-readable error line goes to
  **stderr**. Piping stdout into a JSON parser therefore always works.
- A failure is a **non-zero exit code**: `3` = no target matched, `4` =
  the backend could not be reached, `5` = the action did not take effect.
  The message names the cause and the parameters that were in play.

```powershell
# machine-readable
eyes --json detect --window 123456 --automation-id "SendButton"
eyes --json app state --title-contains "Any VPN" --process-name "Any VPN"
```

### First showcase — 飞书 client

See [`examples/feishu_first_test.py`](examples/feishu_first_test.py).

```powershell
python examples\feishu_first_test.py --dry-run
```

---

<a id="architecture"></a>

## Architecture

```
┌────────────────────────────────────────────────────────────────┐
│  L5  Orchestration    Codex / Claude / Cursor / 自定义 Agent     │
├────────────────────────────────────────────────────────────────┤
│  L4  AI Layer         Intent → Element (LLM 驱动 + 缓存)        │
├────────────────────────────────────────────────────────────────┤
│  L3  Resolver         selector → coord   (UIA / Vision / Hint)  │
├────────────────────────────────────────────────────────────────┤
│  L2  Perceive         capture + detect    (UIA / AX / AT-SPI)    │
├────────────────────────────────────────────────────────────────┤
│  L1  Actuate          mouse / key / drag  (Win32 / CG / XTest)  │
├────────────────────────────────────────────────────────────────┤
│  L0  Platform         Windows (UIA) - macOS/Linux planned       │
└────────────────────────────────────────────────────────────────┘
```

See [`docs/architecture.md`](docs/architecture.md) for the full design.

---

<a id="record"></a>

## Record & Replay (v0.2.0)

**No screenshots, no vision model required.** Every recorded step is a structured `(element, action)` pair — pure text that any LLM can read, reason about, and replay.

### Two capture paths (not mutually exclusive)

| Path | How | When |
|---|---|---|
| **A — Show** | Human demonstrates; `Recorder.record_click()` / `record_type()` captures element schema | monkeybot-style, real workflow |
| **B — Prompt** | Human describes intent; LLM or optional JEV backend generates a `Trajectory` from prompt + element inventory | WorkBuddy-style, zero-demo |

### Quick example

```python
from openeyes.record import Recorder, Replayer, to_natural_language
from openeyes.record.export import to_codex_skill

# Path A: show
r = Recorder()
r.start(name="rotate-vpn", app_name="AnyVPN")
r.record_click(hwnd, element, intent="open VPN client")
r.record_click(hwnd, element, intent="click Connect")
r.record_wait(2000, intent="wait for tunnel")
traj = r.stop()

# Inspect
print(to_natural_language(traj))       # plain English steps
print(to_codex_skill(traj))            # Codex SKILL.md

# Replay (dry-run)
report = Replayer(dry_run=True).replay(traj)
```

### CLI dry-run

```powershell
eyes record replay trajectory.jsonl --json
```

### Export formats

| Format | Use case |
|---|---|
| `to_codex_skill()` | Generate a Codex / OpenAI skill from recording |
| `to_action_receipt()` | ai-action-trace-receipt-standard v0.5 YAML audit trail |
| `to_opencua_trajectory()` | OpenCUA AgentNet-compatible JSONL |
| `merge_trajectories()` | Build a DAG from N recordings of the same workflow |
| `to_natural_language()` | Plain-English step list for LLM consumption |

### Demos

```powershell
python examples\record_demo_mock.py        # no UI needed — fake elements
python examples\record_demo_prompt.py      # LLM generates trajectory from prompt
python examples\record_demo_real.py        # real Edge window (needs openeyes installed)
```

`generate_trajectory_from_prompt(..., backend="jev")` uses the deterministic
fake JEV scorer by default and switches to the real System One wire when
`TYPESAFE_API_KEY` is configured, or when `TYPESAFE_BASE_URL` points at a
loopback node (a local XERJ node may answer keyless).

The thresholds follow the **scale** of the answer, not the scorer: the hosted
judge answers with a probability and keeps `0.85` / `0.60`; a local history
vote answers with a support share (the winner's share of the vote weight)
and gets its own measured profile, per language family (Chinese default) - see
[`docs/jev-local-judge.md`](docs/jev-local-judge.md).

```powershell
$env:TYPESAFE_BASE_URL = "http://127.0.0.1:9201"     # local XERJ node
$env:XERJ_API_KEY = (Get-Content D:\AI-Portable\xerj-data\admin.key -Raw).Trim()
python examples\systemone-local-acceptance.py          # one JSON line, exit 0
python examples\seed-systemone-history.py --index judgements --recreate            # Chinese default
python examples\systemone-calibration.py               # measure the judge
python examples\seed-systemone-history.py --index judgements --recreate --lang en  # English opt-in
```

See [`docs/RECORD_DESIGN.md`](docs/RECORD_DESIGN.md) for the full design rationale.

---

<a id="roadmap"></a>

## Backend priority & degradation chain (v0.2.0)

The rule the 2026 computer-use field converged on: **reach for structure first,
pixels last.** openEyes makes that executable instead of a doc line.

```
uia  ->  cdp  ->  vision
accessibility tree   DOM   screen parsing
```

```bash
# which tier answers for this window, and why did the earlier ones fall through?
eyes resolve --window 1234 --pretty

# turn OmniParser v2 detections into structured elements (no model needed)
eyes vision --detections det.json --pretty
```

```python
from openeyes import resolve_elements

result = resolve_elements(hwnd=1234, port=9222)
print(result.used_backend)              # "uia" | "cdp" | "vision" | None
print([a.to_dict() for a in result.attempts])
```

The vision tier is *parsing-style*: the screen is parsed into the same
`Element[]` shape the other backends emit, so the selector, hint and decision
layers work unchanged. See [docs/architecture.md](docs/architecture.md).

---

## Roadmap

- [x] **v0.1.0** — MVP: UIA capture/detect/click + CLI + MCP + 飞书 first showcase
- [x] **v0.2.0** — Record & Replay: show-and-prompt trajectory capture, no vision required
- [x] **v0.2.0** — Parsing-style vision backend (OmniParser v2) + structured-first degradation chain
- [ ] **v0.3.0** — macOS (AXUIElement) + Linux (AT-SPI)
- [ ] **v0.3.0** — Florence-2 vision backend
- [ ] **v0.4.0** — Vimium-style letter hint overlay
- [ ] **v0.5.0** — LLM intent resolver
- [ ] **v0.6.0** — Audit log + replay
- [ ] **v1.0.0** — Rust hot path + cross-platform binaries

---

## Quality gate

Lint, tests, encoding and the MCP contract are one command, and CI runs the
same steps on every push:

```powershell
pwsh -NoProfile -File examples\quality-gate.ps1
```

| Step | What it proves |
|---|---|
| `ruff check .` | the tree is lint-clean under the repo's own rule set |
| `python examples/bom-sweep.py` | every source file is UTF-8 **without** BOM |
| `python examples/mcp-stdio-probe.py` | the MCP server starts and exposes the 19-tool contract |
| `python -m pytest tests/ -q` | the behaviour holds |

## Repository layout

```
openeyes/
├── openeyes/              # main package
│   ├── core/              # windows + capture + detect / click primitives
│   │   ├── windows.py     # EnumWindows wrapper
│   │   ├── capture.py     # PIL.ImageGrab wrapper
│   │   ├── schema.py      # Element / WindowInfo dataclasses
│   │   ├── selector.py    # element find / filter
│   │   ├── actuator.py    # mouse / hotkey / type_text façade
│   │   ├── tunnel.py      # SSH local forward to a remote browser
│   │   └── hints.py       # Vimium-style letter hint overlay
│   ├── backends/
│   │   ├── uia.py         # Windows UIA backend (pywinauto)
│   │   └── cdp.py         # Edge / Chrome DevTools Protocol backend
│   ├── actuators/
│   │   └── win32.py       # Windows mouse/keyboard input
│   ├── cli/               # `eyes` command
│   │   └── main.py
│   ├── record/             # Record & Replay (v0.2.0, no vision required)
│   │   ├── event.py        # UIAEvent / InputEvent / Trajectory dataclasses
│   │   ├── recorder.py     # Path A: show-and-capture
│   │   ├── replayer.py     # Trajectory → OpenEyes actuate() calls
│   │   ├── abstract.py     # merge_trajectories DAG + natural-language rendering
│   │   ├── generator.py    # Path B: prompt → trajectory (LLM-driven)
│   │   ├── prompt_template.py
│   │   └── export/         # codex_skill / action_receipt / opencua exporters
│   └── mcp/               # MCP server — 19 tools (7 native + 6 browser + 2 structured-first + 1 lifecycle + 2 act-then-prove + 1 discovery)
│       └── server.py
├── docs/
│   ├── architecture.md
│   ├── RECORD_DESIGN.md        # Record & Replay design rationale
│   ├── capability-contract.md   # 19-tool MCP contract (read / write / high-risk)
│   ├── remote-firefox-bidi.md   # remote Firefox over SSH runbook
│   └── dsh-web-acceptance.md    # manual acceptance guide for dsh web
├── examples/
│   ├── feishu_first_test.py
│   ├── remote_firefox_bidi.py    # tunnel + scan/click/type on a remote Firefox
│   ├── record_demo_mock.py      # Record: fake elements, all exports
│   ├── record_demo_prompt.py    # Record: LLM generates trajectory
│   ├── record_demo_real.py      # Record: real Edge window
│   ├── record-out/              # Sample export outputs
│   ├── anyvpn_keepalive.py      # + launch-anyvpn-keepalive.ps1
│   ├── mcp-stdio-probe.py       # MCP stdio mount smoke test
│   ├── dsh-preflight.ps1        # dsh web preflight (read-only)
│   ├── maintenance-round-probes.ps1 # five-probe patrol orchestrator (read-only)
│   ├── dsh-fetch-stall-probe.py # CDP probe for the dsh fetch-stall symptom
│   ├── dsh-session-diagnostic.py
│   ├── dsh-openeyes-mcp.yml     # web profile for dsh
│   └── README.md
├── tests/
│   ├── test_record.py           # Record & Replay unit tests (no UI needed)
│   ├── test_smoke.py
│   ├── test_cdp.py
│   ├── test_hints.py
│   ├── test_mcp_contract.py     # locks the 19-tool contract
│   ├── test_mcp_stdio_probe.py
│   ├── test_maintenance_round_probes.py
│   ├── test_dsh_mount_contract.py
│   └── test_dsh_fetch_stall_probe.py
├── .codex-plugin/
│   └── plugin.json
├── skills/openeyes/
│   └── SKILL.md
├── pyproject.toml
├── LICENSE                 # MIT
└── README.md
```

---

## License

MIT — see [LICENSE](LICENSE).
