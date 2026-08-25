# OpenEyes（开源点睛）

> **AI-friendly computer-use primitives for Windows / macOS / Linux.**
> Capture. Detect. Click. Let any LLM agent drive any desktop GUI.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Windows / macOS / Linux](https://img.shields.io/badge/platform-win%20%7C%20macos%20%7C%20linux-lightgrey)](https://github.com/yangpeng366/openeyes)
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
- **可插拔 vision backend**：OmniParser / Florence-2 / 自训练
- **跨平台**：Windows（首发）/ macOS / Linux，统一抽象

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

OpenEyes fuses these into one MIT-licensed, cross-platform, AI-friendly platform.

### Features

- **AI-friendly element schema** — every interactive element exposes structured JSON
- **Easy click** — UIA hit → bbox center; fallback vision bbox → center; final fallback letter hint
- **Safe by default** — dry-run; full audit trail for state-changing actions
- **MCP-first** — every primitive exposed as an MCP tool to any agent
- **Pluggable vision backend** — OmniParser / Florence-2 / custom
- **Cross-platform** — Windows (first), macOS, Linux, with one unified abstraction

---

<a id="quick-start"></a>

## Quick start

### Windows

```powershell
git clone https://github.com/yangpeng366/openeyes.git
cd openeyes
pip install -e ".[windows,mcp]"

# 1. list visible top-level windows
eyes windows list

# 2. capture a window or the full screen
eyes capture --window 123456 --out shot.png

# 3. enumerate interactive elements
eyes detect --window 123456 --pretty

# 4. click by text (resolves to bbox center)
eyes click --window 123456 --name-contains "Submit" --dry-run
eyes click --window 123456 --name-contains "Submit" --go

# 5. start MCP server (for Codex / Claude / Cursor)
eyes-mcp
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
│  L0  Platform         Windows / macOS / Linux                   │
└────────────────────────────────────────────────────────────────┘
```

See [`docs/architecture.md`](docs/architecture.md) for the full design.

---

<a id="roadmap"></a>

## Roadmap

- [x] **v0.1.0** — MVP: UIA capture/detect/click + CLI + MCP + 飞书 first showcase
- [ ] **v0.2.0** — Vision backend (OmniParser v2 / Florence-2)
- [ ] **v0.3.0** — macOS (AXUIElement) + Linux (AT-SPI)
- [ ] **v0.4.0** — Vimium-style letter hint overlay
- [ ] **v0.5.0** — LLM intent resolver
- [ ] **v0.6.0** — Audit log + replay
- [ ] **v1.0.0** — Rust hot path + cross-platform binaries

---

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
│   │   └── hints.py       # Vimium-style letter hint overlay
│   ├── backends/
│   │   ├── uia.py         # Windows UIA backend (pywinauto)
│   │   └── cdp.py         # Edge / Chrome DevTools Protocol backend
│   ├── actuators/
│   │   └── win32.py       # Windows mouse/keyboard input
│   ├── cli/               # `eyes` command
│   │   └── main.py
│   └── mcp/               # MCP server — 13 tools (7 native + 6 browser)
│       └── server.py
├── docs/
│   ├── architecture.md
│   ├── capability-contract.md   # 13-tool MCP contract (read / write / high-risk)
│   └── dsh-web-acceptance.md    # manual acceptance guide for dsh web
├── examples/
│   ├── feishu_first_test.py
│   ├── anyvpn_keepalive.py      # + launch-anyvpn-keepalive.ps1
│   ├── mcp-stdio-probe.py       # MCP stdio mount smoke test
│   ├── dsh-preflight.ps1        # dsh web preflight (read-only)
│   ├── dsh-fetch-stall-probe.py # CDP probe for the dsh fetch-stall symptom
│   ├── dsh-session-diagnostic.py
│   ├── dsh-openeyes-mcp.yml     # web profile for dsh
│   └── README.md
├── tests/
│   ├── test_smoke.py
│   ├── test_cdp.py
│   ├── test_hints.py
│   ├── test_mcp_contract.py     # locks the 13-tool contract
│   ├── test_mcp_stdio_probe.py
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
