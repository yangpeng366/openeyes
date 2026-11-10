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

### viewport 左上角屏幕坐标：规范保证 / 实测可得 / 只能估算

`to_screen_point` 需要 viewport 原点（屏幕/设备像素）。这个原点从哪来，
`openeyes.core.viewport` 一处写清，并且把三种来源分开，不互相顶替：

| 层级 | 来源 | 结论 |
| --- | --- | --- |
| 规范保证 | `browser.getClientWindows` 的 `x`/`y`：web-exposed screen area 左/上边缘到**客户端窗口**左/上边缘的 CSS 像素 | 是真事实，但**不是答案**：它给的是整个窗口（含标签栏、地址栏、窗口边框），规范没有任何字段暴露 viewport 在窗口内的偏移 |
| 实测可得 | Firefox `window.mozInnerScreenX` / `mozInnerScreenY`（CSS 像素）乘 `window.devicePixelRatio` | 本机实测与 Win32 `ClientToScreen` 逐像素一致，是当前唯一精确来源（Gecko 扩展，非 BiDi 命令） |
| 只能估算 | `outerHeight - innerHeight` 当 chrome 高度、或假定窗口在 `(0, 0)` | 一律**拒绝**：差 90 px 的"成功点击"会落在标题上而不是按钮上 |

实测记录（Firefox 159 / Windows 11，2240x1400 @130%，`devicePixelRatio`
1.3043478260869565，窗口由 `SetWindowPos` 移到 (400,260)）：

```
mozInnerScreenX/Y      313.5667, 272.1667   CSS px
x dpr                  409.0,    355.0      device px
Win32 ClientToScreen   409,      260        + 95 px chrome = 355
browser.getClientWindows  x=307 y=199 w=843 h=613  (window, CSS px)
```

契约（`tests/test_viewport_origin.py` 锁住）：

- 有测量值 → `BidiConnection.viewport_origin()` 返回 `ViewportOrigin`
  （同时带 CSS 值、dpr、来源），`bidi.screen_point()` /
  `backends.browser.screen_point()` 用它做换算。
- 没有测量值 → 抛 `MissingViewportOrigin`，报错里写明：试过哪个属性、
  `browser.getClientWindows` 报了什么窗口矩形、为什么窗口不等于 viewport、
  两条出路（自己传 `viewport_origin=(x, y)`，或用页内派发
  `origin='viewport'`）。**绝不**退化成 `(0, 0)` 或窗口原点。
- 页内点击路径不变：`bidi.click_center` 仍走 `input.performActions` 的
  `origin: "viewport"`，不需要屏幕原点，因此不受上述拒绝影响；
  `cdp` 后端没有原点来源，`browser.viewport_origin("cdp")` 直接拒绝而
  不是猜一个。

### CDP（Edge/Chromium）不做屏幕坐标

和 BiDi 不同，CDP 连「窗口矩形」都只在特定连接上给得出来，viewport 的屏幕
原点则完全没有字段。所以 `cdp.viewport_origin()` 存在，但**只做拒绝**：
它先把页面能说的都读出来，再明确拒绝，并给出两条出路。

协议字段各自是什么（本机 Edge 152 实测，逐条核对）：

| 字段 | 坐标系与语义 | 是不是答案 |
| --- | --- | --- |
| `Page.getLayoutMetrics` 的 `visualViewport.clientWidth/Height` | 渲染器给的视口尺寸，**设备像素**（本次 1721x1077 @ dpr 1.302），不含任何屏幕偏移 | 只有尺寸 |
| `Browser.getWindowForTarget` / `Browser.getWindowBounds` | **窗口**矩形，CSS 像素（DIP）；`left=100,top=80,width=702,height=522` 对应物理 `(130,104) 912x678` | 是窗口，含边框与浏览器 UI |
| `Runtime.evaluate` 的 `screenX`/`screenY` | **窗口**左/上边缘到屏幕原点，CSS 像素 | 是窗口原点，不是视口原点 |
| `Runtime.evaluate` 的 `outerWidth`/`outerHeight` | **窗口**外框尺寸，CSS 像素 | 窗口尺寸 |
| `Runtime.evaluate` 的 `innerWidth`/`innerHeight` | **视口**尺寸，CSS 像素（含滚动条，不含浏览器 UI） | 视口尺寸；与 `outer*` 的差并**不是** chrome 高度 |

实测（可见 Edge 152.0.4191.19，Windows 11 25H2，2240x1400 @130%，
`devicePixelRatio` 1.3020833730697632；几何只用 `Browser.setWindowBounds`
驱动，页面在自身 `(0,0)` 画一块 40x40 洋红，`PrintWindow(PW_RENDERFULLCONTENT)`
取得窗口位图后定位该块；`Target.activateTarget` 保证渲染器报的是当前几何）：

```
窗口 bounds(100,80) 700x520
  Win32 GetWindowRect      (130,104) 912x678      ClientToScreen (139,104)
  screenX/Y                100, 80   x dpr -> (130,104)   = 窗口原点，逐像素相等
  outer / inner            702x522 / 680x430              outer-inner = 22x92 CSS
  像素里的视口原点          (145,209)  ->  相对窗口 (15,105)
  差值：窗口原点 - 视口原点  (15,105)；而 outerHeight-innerHeight 预测 120 px

窗口 bounds(40,30) 900x640
  Win32 GetWindowRect      (52,39) 1172x834
  screenX/Y                40, 30   x dpr -> (52,39)     = 窗口原点
  像素里的视口原点          (67,144)  ->  相对窗口 (15,105)   同一偏移

Browser.setWindowBounds fullscreen
  Win32 GetWindowRect      (0,0) 2240x1400
  screenX/Y                0, 0     x dpr -> (0,0)
  outer / inner            1721x1076 / 1721x1077          outer-inner = 0x-1
  像素里的视口原点          (0,0)                           与窗口边缘重合
```

三档归位：

- **规范保证**：没有。CDP 没有任何字段描述「viewport 左上角在屏幕上的位置」，
  `getLayoutMetrics` 只给 CSS 像素下的尺寸，`getWindowBounds` 只给窗口矩形。
- **实测可得**：只有全屏这一个状态经实测重合（窗口 `(0,0)`、视口 `(0,0)`，差值
  0 像素）。但它不是可判定的：同一次全屏里 Chromium 报 `outerHeight=1076`
  对 `innerHeight=1077`（差 1 px），任务里假设的
  `outerWidth===innerWidth && outerHeight===innerHeight` 在真机上**不成立**；
  而且全屏下浏览器 UI 仍可能压住顶部——带 `--no-sandbox` 的实例在同样全屏
  状态下，洋红块出现在窗口 `(0,55)`，说明 infobar 把视口又推下去了 55 px。
  要么放宽成「差 1-2 px 也算全屏」，那就是估算；要么只在精确相等时返回，那在
  本机一次都不会成立。两条路都不满足「未实测不返回」，因此不实现返回分支。
- **只能估算**：`screenX*dpr` 当视口原点、`outerHeight-innerHeight` 当 chrome
  高度。实测前者差 105 px（纵向）、后者差 15 px，一律拒绝。

于是 `openeyes/backends/cdp.py` 里：

```python
def viewport_origin(conn, element=None):
    report = conn.evaluate(VIEWPORT_ORIGIN_JS)      # 只读，不点任何东西
    raise MissingViewportOrigin(
        cdp_origin_message(report, bounds=window_bounds(conn)))
```

拒绝信息里写明：页面报了什么（`screenX/screenY/outer*/inner*/dpr`）、
`Browser.getWindowBounds` 报的窗口矩形（页面级会话上常被拒，
`Browser window not found`，因此是 best-effort）、本机实测的 15/105 px 偏移、
以及两条出路：调用方自己传 `viewport_origin=(x, y)`（屏幕/设备像素），或改走
页内派发 `Input.dispatchMouseEvent`（它本来就吃 viewport 坐标，不需要屏幕原点）。
`browser.viewport_origin("cdp", …)` 直接透传这个拒绝，`browser.screen_point`
因此也不会给出估算坐标。

复现命令（可见 Edge，非无头；跑完自动恢复被临时最小化的窗口）：

```powershell
python C:\Users\47037\AppData\Local\Temp\oe-cdp-origin-probe8.py
```

前置条件与后续可选来源：

- `mozInnerScreenX/Y` 只保证在 Gecko 上存在；Chromium 系浏览器必须由调用方
  提供原点，或改走页内点击。
- CSS→设备像素的换算要求宿主进程是 per-monitor DPI aware：
  `openeyes.core.dpi.ensure_dpi_awareness()` 必须先跑，否则 130% 下的
  `SetCursorPos` 会落在虚拟化坐标里。
- 多屏/负坐标副屏上的 `mozInnerScreenX/Y` 是否已经是虚拟桌面坐标，本机
  （单屏 130%）**未实测**，已列为待测项（见 
  `examples/multi-monitor-smoke.py`）。
- 可选来源（尚未接入）：Chromium 系若将来在 BiDi 暴露 viewport 矩形，
  可由 `clientWindow` 矩形 + 该矩形换算；目前没有该字段，故不做估算。

两种取数模式，结果完全一致：

- **file 模式** —— `detections_path=` 指向已保存的检测 JSON，无需模型与网络；
- **endpoint 模式** —— `endpoint=` 指向一个接受 `{"image": "<base64 png>"}`
  的 HTTP 服务。

CLI 出口：

```bash
eyes vision --detections det.json --pretty          # 检测结果 -> 元素
eyes resolve --window 1234 --pretty                 # 谁答上来了 + 降级留痕
```