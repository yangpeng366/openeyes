# OpenEyes Architecture

> **Full design doc**: see the Feishu design book at
> https://my.feishu.cn/docx/Ul6gdMULGo5VfzxDVAYcVoX6n9e
>
> Local snapshot: `02_项目推进/openeyes/DESIGN.md` in the OpenEyes project
> tracking Base.

## 5 layers

```
┌────────────────────────────────────────────────────────────────┐
│  L5  Orchestration    Codex / Claude / Cursor / 自定义 Agent     │
├────────────────────────────────────────────────────────────────┤
│  L4  AI Layer         Intent -> Element (LLM driven + cache)   │
├────────────────────────────────────────────────────────────────┤
│  L3  Resolver         selector -> coord  (UIA / Vision / Hint)  │
├────────────────────────────────────────────────────────────────┤
│  L2  Perceive         capture + detect   (UIA / AX / AT-SPI)    │
├────────────────────────────────────────────────────────────────┤
│  L1  Actuate          mouse / key / drag (Win32 / CG / XTest)   │
├────────────────────────────────────────────────────────────────┤
│  L0  Platform         Windows / macOS / Linux                  │
└────────────────────────────────────────────────────────────────┘
```

## Core data flow

```
agent(intent="click the Submit button")
        |
        v
[Resolver.resolve(intent, snapshot)] --> Element{control_type, name, bbox, score, backend}
        |                                                |
        |                                                v
        |                                [Capture.window(hwnd)] --> PNG
        |                                                |
        |                                                v
        |                                [Backend.detect()] --> [Element, ...]
        |
        v
[Actuator.click(element)] --> mouse_event(bbox.center)
        |
        v
[Audit.log(screenshot_before, screenshot_after, decision)]
```

## Element schema (AI-friendly JSON)

```json
{
  "backend": "uia",
  "control_type": "Button",
  "name": "Secure my connection",
  "automation_id": "SecureButton",
  "class_name": "Button",
  "bbox": {"x": 3284, "y": 962, "w": 754, "h": 60},
  "center": {"x": 3661, "y": 992},
  "score": 1.0,
  "hint": null,
  "interactive": true,
  "state": {"enabled": true, "visible": true, "focused": false, "selected": false},
  "parent_chain": ["ApplicationFrameWindow", "Window", "Pane"]
}
```

## Backend abstraction

```python
class Backend(Protocol):
    name: str
    def list_windows(self) -> list[WindowInfo]: ...
    def capture(self, hwnd: int | None) -> Image: ...
    def detect(self, hwnd: int, image: Image) -> list[Element]: ...
```

Built-in backends:
- `uia` (Windows, free, no ML) — production-ready
- `ax` (macOS, free, no ML) — planned v0.3.0
- `atspi` (Linux, free, no ML) — planned v0.3.0
- `vision-omniparser` (any, GPU required) — planned v0.2.0
- `vision-florence` (any, CPU ok) — planned v0.2.0

## Roadmap

- [x] **v0.1.0** — MVP: UIA capture/detect/click + CLI + MCP + 飞书 first showcase
- [ ] **v0.2.0** — Vision backend (OmniParser v2 / Florence-2)
- [ ] **v0.3.0** — macOS (AXUIElement) + Linux (AT-SPI)
- [ ] **v0.4.0** — Vimium-style letter hint overlay
- [ ] **v0.5.0** — LLM intent resolver
- [ ] **v0.6.0** — Audit log + replay
- [ ] **v1.0.0** — Rust hot path + cross-platform binaries