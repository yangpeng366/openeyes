# Scene text — 把窗口读成文字（给看不见图片的 agent）

## 为什么

现在 agent 了解一个窗口，只有两条路：读无障碍树（UIA），或者看截图。前者对 Electron /
canvas 界面常常只有窗口壳（Trae CN 只有约 19 个壳节点），后者对不支持图片的模型直接不可用。

OpenCV + OCR 补的是中间那一档：**把像素翻译成结构化的文字场景**，agent 拿到的不是一张图，
而是一份可读、可定位、可执行的清单：

```
窗口 Trae CN 1886x1183 来源=ocr 元素=42
[按钮] 新任务 @(77,167) 0.99
[文本] SOLO @(47,98) 0.99
[输入框] 描述想完成的工作 @(992,1031) 0.88
```

判据留在代码里，解释留给模型：阈值、归组、去重都是确定性的；「这看起来像什么」由模型读
文字摘要后自己决定。

## 降级链

`uia → ocr → opencv`：能拿无障碍树就用它（自带名字与控件类型）；拿不到就用 OCR 读文字，
再由 OpenCV 的轮廓补出没有文字的控件（图标、方框、面板）；两路结果按位置去重合并。

取帧优先 `PrintWindow`（能读被遮挡的后台窗口，实测 Trae CN 可读），失败再退回屏幕抓取
（要求窗口在前台）。

## 契约

### 1. `openeyes/backends/ocr.py`（负责：codex-client）

```python
class OCRUnavailable(RuntimeError): ...

def ocr_engine(engine: Any = None) -> Any:
    """返回可调用的 OCR 引擎；不传则懒加载 rapidocr_onnxruntime 并缓存。"""

def read_text(image: Any, *, engine: Any = None) -> list[dict[str, Any]]:
    """对一张图跑 OCR，返回 [{'text','box','bbox','score'}]。

    image: PIL.Image 或 np.ndarray（RGB，HxWx3）。
    box: 4 点多边形 [[x,y] * 4]，图像像素。
    bbox: (x, y, w, h) 整数，轴对齐外接框。
    score: float 0..1。
    """
```

- 引擎返回两种形态都要吃：`([poly, text, score], ...)` 与 `(results, elapse)`。
- 丢弃空文本；分数裁剪到 0..1；结果按 (y, x) 排序。
- 引擎缺失（未装 rapidocr_onnxruntime）抛 `OCRUnavailable`，调用方降级，不崩。

### 2. `openeyes/core/scene.py`（负责：codebuddy-client）

```python
def describe_window(hwnd: int, *, source: str = "auto", include_ocr: bool = True,
                    include_shapes: bool = True, max_items: int = 250,
                    region: tuple[int, int, int, int] | None = None,
                    detect: Any = None,
                    capture: Any = None, engine: Any = None) -> dict[str, Any]:
```

返回：

```json
{
  "ok": true,
  "source": "uia | ocr | mixed",
  "hwnd": 21174116,
  "window": {"title": "...", "class_name": "...", "rect": [l, t, r, b],
             "size": [w, h], "dpi_scale": 1.3021},
  "capture": {"method": "printwindow | screen", "occluded": false},
  "elements": [
    {"kind": "button|label|input|checkbox|icon|panel|menu|unknown",
     "text": "新任务", "bbox": {"x": 24, "y": 143, "w": 106, "h": 48},
     "center": {"x": 77, "y": 167}, "score": 0.99,
     "source": "uia|ocr|opencv", "interactive": true,
     "state": {"focused": false, "enabled": true}}
  ],
  "text_lines": ["SOLO", "新任务", "..."],
  "shapes": [{"kind": "rect|square|icon|bar", "bbox": {...}, "area": 1234}],
  "counts": {"button": 2, "label": 38},
  "elapsed_ms": 1234,
  "error_code": null,
  "notes": []
}
```

规则：

- `capture`：可调用的取帧函数 `capture(hwnd) -> PIL.Image`；不传则用 `openeyes.core.capture.capture_window_best`，它先试 `PrintWindow`（窗口自己画，能读被遮挡的后台窗口），画面近乎空白时退回屏幕抓取，并回报方法名（`printwindow` / `screen`）。若 PrintWindow 出来的画面读不出任何文字，再抓一次屏幕（仅当窗口确实在屏幕上）。
- `info`：可调用的 `info(hwnd) -> dict`，返回 title / class_name / rect / size / dpi_scale；不传则自己读窗口。
- `detect`：可调用的 `detect(hwnd) -> list[Element]`；不传则用 `openeyes.core.selector.detect_elements`。
- `engine`：注入的 OCR 引擎（测试用），不传则走 `openeyes.backends.ocr.ocr_engine()`。

- `source="auto"`：UIA 与 OCR 都跑，按位置去重合并（有 UIA 元素时，`source` 为 `mixed`；
  只有 OCR 可用时为 `ocr`；只有 UIA 可用时为 `uia`）。`source="uia"` / `"ocr"` 只跑一路。
- 去重三则：①IoU > 0.3；②中心点落在某个 UIA 元素内；③被 UIA 元素覆盖过半。
  但②③只认**尺寸可比**的元素（高不超过文字行的 3 倍、宽不超过 4 倍）——Chromium 会把整窗容器报成元素，
  否则一行字会被容器吞掉，页面内容全丢。
- Chromium 的外壳节点（名字以 View / Pane / Layer / Surface / Container 结尾）不入清单：那是结构不是内容。
- 坐标越界（落在窗口矩形之外）的元素丢弃：虚拟桌面上的 Chromium 会报出负坐标，照着点会点错地方。
- 有 OCR 文字时，只保留 UIA 里**可交互**的元素（按钮 / 输入框 / 勾选框）；其余外壳节点只会淹没摘要。
- OCR 归行：按 (y, x) 排序；垂直重叠 > 60% 且水平间隙 < 1.5 倍行高的项归为一行；
  中文之间不插空格。
- 归类启发式（阈值写在代码里）：
  - 文字落在 OpenCV 检出的面板轮廓内 → `panel`；
  - 短文字（≤ 6 字符）且落在圆角矩形轮廓内 → `button`；
  - 无文字的小方框 / 小圆 → `checkbox` 或 `icon`；
  - 其余文字 → `label`。
- OpenCV 形状：`Canny` + 形态学闭运算 + `findContours`；过滤小噪声；分类
  `rect`（宽 > 2 倍高）、`square`（近似正方且面积在 64..16000 px²）、`icon`（更小）、
  `bar`（细长横条）。
- 健壮性：OCR 引擎缺失 → 仍返回 UIA 场景，并在 `notes` 写明；若一条元素都没拿到，
  `ok=false` 且 `error_code` 取 `OCR_UNAVAILABLE` / `CAPTURE_FAILED` / `INVALID_HWND` /
  `WINDOW_MINIMIZED` 之一。
- 不得抛异常给调用方（除参数非法外），失败要落到 `error_code`。

### 3. `openeyes/core/scene_text.py`（负责：opencode-client）

```python
def render_scene(scene: dict[str, Any], *, max_lines: int = 200,
                 max_chars: int = 6000) -> str:
```

- 首行：`窗口 <title> <w>x<h> 来源=<source> 元素=<n>`
- 每元素一行：`[按钮] <text> @(<cx>,<cy>) <score>`；中文类别名映射：
  button=按钮、label=文本、input=输入框、checkbox=复选框、icon=图标、panel=面板、
  menu=菜单、unknown=未知；无文字时省略 `<text>`。
- 按 (y, x) 排序；超出 `max_lines` / `max_chars` 截断并留一行省略说明。
- 纯 ASCII 标点 + 中文类别名；不加 ANSI 颜色。

### 4. CLI 与 MCP（负责：Codex 主模型）

- `eyes scene --window <hwnd> [--json] [--format text|json] [--max-items N]
  [--source auto|uia|ocr] [--no-shapes]`：默认打印文字摘要，`--json` 输出完整场景
  （JSON 里带 `text_digest` 字段）。
- MCP 工具 `scene_describe(hwnd, source?, max_items?, format?)` → 场景 JSON。

## 实测（本机，2026-11-14）

| 目标 | 结果 |
| :- | :- |
| Trae CN（无 UIA 内容） | `eyes scene` 读出标题、菜单栏、`SOLO`、文件页签；58 个元素，来源 `mixed` |
| 取帧 | `PrintWindow` 读出被 Codex 窗口盖住的 Trae 真页面；Qt（EverNote）返回空面，自动退回屏幕抓取并注明 |
| 耗时 | CPU RapidOCR 在 1886x1183 上约 15–20 s（与分辨率关系不大，瓶颈是检测模型）；锚点探针仍是 0.15 s 的快路 |
| 飞书（Chromium） | UIA 树很吵（`Pane` / `ContentsView` 之类），按上面的去重与外壳过滤后只留可交互控件与 OCR 文字 |

## 验收

- `python -m pytest -q tests/test_ocr_backend.py tests/test_scene.py tests/test_scene_text.py`
- `python -m ruff check openeyes tests`
- 真机：Trae CN（无 UIA 内容）应读到 `SOLO` / `新任务` / 输入框占位；记事本（有 UIA）
  应走 `uia` 优先且元素带名字。
- 源文件 UTF-8 无 BOM。

## 不做什么

- 不做通用视觉理解：形状只到「面板 / 方框 / 图标 / 条」这一档，语义留给模型。
- 不替模型做判断：输出是证据与定位，不是「该点哪里」的结论。
- 不做后台自动化：取帧优先 PrintWindow，但动作仍受仓库既有前台闸门约束。
