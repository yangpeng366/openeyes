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
- `cdp` (Chromium DOM, free, no ML) — production-ready
- `lightpanda` (headless Chromium via CDP, free, no ML) — production-ready
- `vision` (screen parsing, OmniParser v2 compatible) — available in v0.2.0
- `ax` (macOS, free, no ML) — planned v0.3.0
- `atspi` (Linux, free, no ML) — planned v0.3.0
- `vision-florence` (any, CPU ok) — planned v0.2.1

## Roadmap

- [x] **v0.1.0** — MVP: UIA capture/detect/click + CLI + MCP + 飞书 first showcase
- [x] **v0.2.0** — Vision backend (OmniParser v2) + structured-first degradation chain
- [ ] **v0.3.0** — macOS (AXUIElement) + Linux (AT-SPI)
- [ ] **v0.4.0** — Vimium-style letter hint overlay
- [ ] **v0.5.0** — LLM intent resolver
- [ ] **v0.6.0** — Audit log + replay
- [ ] **v1.0.0** — Rust hot path + cross-platform binaries
## 后端优先级与降级链（v0.2.0）

2026 年 computer-use 的行业共识：**能走结构化就别截图，截图只当最后手段。**
openEyes 把这条规则从「文档里的一句话」落成可执行代码 —— `openeyes.core.resolve`。

```
uia  ->  cdp  ->  vision
无障碍树     DOM      像素解析
```

每一级都产出与其它后端同构的 `Element[]`，因此 `find_elements` / `assign_hints` /
决策层对三者一视同仁；区别只在 `backend` 标签与 `score`：

| 后端 | 语义 | score | 何时用 |
| --- | --- | --- | --- |
| `uia` | 桌面应用无障碍树 | 1.0 | 原生应用（飞书 / VS Code / Excel） |
| `cdp` | Chromium DOM / a11y tree | 1.0 | 网页（含已登录态） |
| `vision` | 屏幕解析（OmniParser v2 等） | < 1.0 | canvas / 图片 / 无无障碍树的远端桌面 |

`resolve_elements()` 返回 `ResolveResult`：元素 + `used_backend` + 每一级的
`attempts`（成功、元素数、失败原因）。调用方因此能回答「为什么退到了下一级」，
这比直接看图点坐标可审计得多。

```python
from openeyes import resolve_elements

result = resolve_elements(hwnd=1234, port=9222)
print(result.used_backend, [a.to_dict() for a in result.attempts])
```

### Vision 后端是「解析式」的，不是「看图式」的

`openeyes.backends.vision` 不让模型对着像素点坐标，而是先把屏幕解析成结构化
元素。输入兼容 OmniParser v2 官方输出格式：

```json
[[[10, 20, 110, 60], "button"], [[10, 80, 210, 120], "text"]]
```

也兼容带置信度、`boxes/labels/scores` 并列、逐项 dict 等常见变体。坐标语义
统一为窗口相对：`origin=(left, top)` 是**窗口左上角在图像中的位置**，图像像素
减去它得到窗口坐标（窗口内截图传 `(0, 0)`；整屏截图也要传窗口原点）。物理
点击前统一由 `openeyes.core.coords.to_screen_point` 补回窗口原点；漏传
`origin` 时输出等价于屏幕绝对坐标，此时直接 `click_xy`，不可再补一次。

### 坐标空间只有两种：window 与 viewport

`openeyes.core.coords` 是坐标语义唯一写下来的地方，`BACKEND_SPACES` 一
处登记：

| 后端 | 空间 | 点击前加回的 origin |
| --- | --- | --- |
| `uia` / `vision` | window | `GetWindowRect` 的 `(left, top)` |
| `cdp` / `bidi` | viewport | viewport 左上角的屏幕坐标 |

`cdp` 与 `bidi` 是**同一个空间**：两者量到的都是 DOM 
`getBoundingClientRect()`，BiDi 派发 `pointerMove` 时也明确以
`origin: "viewport"` 为准，因此都不加窗口原点。前端 `click_by_selector`
与 CLI 的 pixel 路径都在物理点击前经 `to_screen_point` 换算；未登记的后
端不会被猜成某个空间，而是抛 `UnknownCoordinateSpace`。

两种取数模式，结果完全一致：

- **file 模式** —— `detections_path=` 指向已保存的检测 JSON，无需模型与网络；
- **endpoint 模式** —— `endpoint=` 指向一个接受 `{"image": "<base64 png>"}`
  的 HTTP 服务。

CLI 出口：

```bash
eyes vision --detections det.json --pretty          # 检测结果 -> 元素
eyes resolve --window 1234 --pretty                 # 谁答上来了 + 降级留痕
```