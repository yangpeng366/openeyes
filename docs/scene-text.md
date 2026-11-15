# Scene text — 把窗口读成文字（给看不见图片的 agent）

## 为什么

现在 agent 了解一个窗口，只有两条路：读无障碍树（UIA），或者看截图。前者对 Electron /
canvas 界面常常只有窗口壳（Trae CN 只有约 19 个壳节点），后者对不支持图片的模型直接不可用。

OpenCV + OCR 补的是中间那一档：**把像素翻译成结构化的文字场景**，agent 拿到的不是一张图，
而是一份可读、可定位、可执行的清单：

```
窗口 Trae CN 1886x1183 @(59,80) 来源=ocr 像素=printwindow 可信=是 元素=42
[按钮] 新任务 @(77,167) 0.99
[文本] SOLO @(47,98) 0.99
[输入框] 描述想完成的工作 @(992,1031) 0.88
```

首行给三样坐标证据：窗口尺寸（`window.size`，元素坐标系就是这套像素）、窗口在屏幕上的原点
`@(x,y)`（把 `center` 变成屏幕点只需要加这一次）、以及像素从哪来（`像素=`）和能不能信（`可信=`）。
读不到时第二行是 `失败 <error_code>: <原因>`，不再让 agent 从空清单里猜是哪一层挂了。

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
  "requested_source": "auto | uia | ocr",
  "hwnd": 21174116,
  "window": {"title": "...", "class_name": "...", "rect": [l, t, r, b],
             "size": [w, h], "dpi_scale": 1.3021},
  "capture": {"method": "printwindow | screen | injected | null",
              "occluded": false, "size": [w, h], "region": null,
              "verified": true, "suppressed": []},
  "elements": [
    {"kind": "button|label|input|checkbox|icon|panel|menu|caret|unknown",
     "text": "新任务", "bbox": {"x": 24, "y": 143, "w": 106, "h": 48},
     "center": {"x": 77, "y": 167}, "score": 0.99,
     "source": "uia|ocr|opencv", "interactive": true,
     "state": {"focused": false, "enabled": true}}
  ],
  "text_lines": ["SOLO", "新任务", "..."],
  "shapes": [{"kind": "bar|panel|checkbox|icon|caret", "bbox": {...}, "area": 1234}],
  "counts": {"button": 2, "label": 38},
  "elapsed_ms": 1234,
  "error_code": null,
  "reason": null,
  "notes": []
}
```

`capture.verified`：`true` 表示这帧能证明就是目标窗口的像素（`printwindow` / 调用方注入的帧 /
当前前台窗口的屏幕抓取），`false` 表示不能证明——此时 OCR 与 OpenCV 结果已被抑制，
`capture.suppressed` 记下抑制了哪两层，`elements` 里不会出现别窗文字。`null` 表示本次没取帧。

### 坐标契约（2026-11-14 修复）

- **所有 `bbox` / `center` 一律是「窗口相对物理像素」**（各层同一坐标系）：
  `x=0, y=0` 就是窗口左上角，不是屏幕左上角。要把点映回屏幕坐标：`screen = bbox.xy + window.rect[:2]`，
  原点只加这一次（CLI/MCP 的摘要首行把 `window.rect[:2]` 直接印成 `@(x,y)`）。
- **UIA 后端按契约输出窗口相对坐标**：`backends/uia.py` 在遍历树时读一次 `GetWindowRect`，
  把元素的绝对屏幕矩形减掉窗口原点（`window_origin()`）；`schema.Element.bbox` 与
  `coords.BACKEND_SPACES["uia"] == "window"` 说的都是这个空间。修复前后端交的是绝对屏幕坐标，
  而 CLI / `click_by_selector` 又按契约再加一次原点，窗口在 (300, 200) 时每次“成功”的点击都偏
  (300, 200)。
- **`window.size` 是这套坐标的尺寸, 且永远是窗口矩形的物理像素**（`GetWindowRect` 的宽高）。
  `capture.size` 另记取帧本身量到的像素。两者只在「取帧路径与窗口度量处于不同 DPI 精度」时不同（DPI 虚拟化）；
  这时帧里的坐标（OCR 文字框 / OpenCV 轮廓）按 `窗口尺寸 / 帧尺寸` 重采样进窗口网格，
  UIA 坐标已经是窗口像素、不做缩放。修复前这条是反的（网格跟帧走、UIA 被放大），
  于是 `window.rect[:2] + center` 得到的是帧里的点，窗口 800x600 而帧 1200x900 时整张清单偏 1.5 倍。
- **越界即不可执行**：换算后中心点落在窗口外的元素直接丢弃（虚拟桌面上的 Chromium 会报出窗外子树，
  实测窗口宽 1313 却有元素 x 到 1674）；跨窗口边界的元素把 bbox 裁到窗口内，`center` 取裁剪后的中点，
  因此清单里任何 `center` 都是窗口内的可点击位置。丢弃数量写在 `notes`（`uia-outside-window: N element(s) dropped`）。
- **窗口原点是必需证据**：`window_origin()` 读不到 `GetWindowRect` 时返回 `None`，`detect_elements()`
  抛 `WindowOriginUnavailable`，scene 记成 `UIA_UNAVAILABLE`。不拿 `(0,0)` 顶替——那会让每条
  UIA 坐标都偏掉一整个窗口原点，而调用方看上去一切正常。
- `_window_info` 在量窗口前先 `ensure_dpi_awareness()`，让 `GetWindowRect`、UIA 与 `SetCursorPos`
  说的是同一套物理像素。

规则：

- `capture`：可调用的取帧函数 `capture(hwnd) -> PIL.Image`；不传则用 `openeyes.core.capture.capture_window_best`，它先试 `PrintWindow`（窗口自己画，能读被遮挡的后台窗口），画面近乎空白时退回屏幕抓取，并回报方法名（`printwindow` / `screen`；注入取帧时报 `injected`）。若 PrintWindow 出来的画面读不出任何文字，再抓一次屏幕——**仅当窗口确实在屏幕上、当前是前台窗口，且 `_screen_view_clear()` 判定视野没有被别人盖住**；不满足时写 `screen fallback skipped: ...` 并且不抓帧。
- **像素必须能证明是目标窗口的**：`printwindow` / `injected` 天然属于目标窗口；`screen` 还需三条同时成立：
  在虚拟屏幕上、是前台窗口、且 6x6 采样点（各向外留 1/32 边距）的 `WindowFromPoint(GetAncestor(..., GA_ROOT))`
  都是本窗口。前台只是必要不充分条件——置顶小窗、输入法候选框、同应用的弹窗都能盖住一个“前台”窗口。
  任一条不成立（包括探测本身失败）整帧像素被丢弃（`capture.verified=false`、`capture.suppressed=["ocr","opencv"]`、
  `notes` 里 `unverified-frame: ...`），`ok=false` 且 `error_code="UNVERIFIED_FRAME"`，
  绝不把别的窗口的文字当成目标页面报给 agent。
- `info`：可调用的 `info(hwnd) -> dict`，返回 title / class_name / rect / size / dpi_scale；不传则自己读窗口。
- `detect`：可调用的 `detect(hwnd) -> list[Element]`；不传则用 `openeyes.core.selector.detect_elements`（Element 坐标已是窗口相对）。
- `engine`：注入的 OCR 引擎（测试用），不传则走 `openeyes.backends.ocr.ocr_engine()`。
- **参数先校验**：`source` 只接受 `auto/uia/ocr`（否则 `INVALID_SOURCE`），`region` 必须是
  `(left, top, right, bottom)` 且 `0 <= left < right`、`0 <= top < bottom`（否则 `INVALID_REGION`）；
  region 超出帧时裁到帧内并写 `region-clamped`，完全在帧外时 `INVALID_REGION`。
  取帧 / `crop` / 形状检测的异常都落进 `notes` 与 `error_code`，不会从 `describe_window` 里向外抛。

- `source="auto"`：UIA 与 OCR 都跑，按位置去重合并（`source` 记的是**实际贡献的层**：有 UIA 元素且另有 OCR/形状为 `mixed`，
  只有 OCR/形状为 `ocr`，只有 UIA 为 `uia`；`requested_source` 记调用方要的那一路）。
- `source="uia"`：**只读无障碍树，绝不初始化/调用 OCR，也不取帧、不出 OpenCV 形状**——包括 PrintWindow
  读出空帧这条路径，不会退到屏幕抓取再 OCR（实测 bug：显式 `uia` 仍跑了 OCR，`ocr_elements=43`，notes 里出现
  `printwindow frame carried no text; fell back to a screen grab`）。`include_shapes=True` 时也只是在 `notes`
  写一条 `shapes-skipped: source=uia reads no pixels`：视觉特征属于像素层，要用就 `--source auto/ocr`。
- `source="ocr"`：只读像素，**不枚举 UIA**（不暗地里回落）。OCR 不可用时 `ok=false`、
  `error_code="OCR_UNAVAILABLE"`，`notes` 里只有 `ocr-unavailable: ...`；要别一层就自己换 `--source`。
- 取帧只在有人要读它时发生：本次确实允许 OCR，或 `source in {"auto", "ocr"}` 且需要 OpenCV shapes。
  因此 `--source uia` **完全不取帧**（`capture.method=null`，`elapsed_ms` 只剩 UIA 遍历）。
- 去重三则：①IoU > 0.3；②中心点落在某个 UIA 元素内；③被 UIA 元素覆盖过半。
  但②③只认**尺寸可比**的元素（高不超过文字行的 3 倍、宽不超过 4 倍）——Chromium 会把整窗容器报成元素，
  否则一行字会被容器吞掉，页面内容全丢。
- Chromium 的外壳节点（名字以 View / Pane / Layer / Surface / Container 结尾）不入清单：那是结构不是内容。
- 坐标越界（换算后中心点落在窗口矩形之外）的元素丢弃；跨边界的元素把 bbox 裁到窗口内，`center` 取裁剪后的中点。
  虚拟桌面上的 Chromium 会报出窗外坐标（实测窗口 1313x882、元素绝对 x 到 1674），照着点会点到别的窗口。
- 有 OCR 文字时，只保留 UIA 里**可交互**的元素（按钮 / 输入框 / 勾选框）；其余外壳节点只会淹没摘要。
- OCR 归行：按 (y, x) 排序；垂直重叠 > 60% 且水平间隙 < 1.5 倍行高的项归为一行；
  中文之间不插空格。
- 归类启发式（阈值写在代码里；形状几何阈值按下面的 `shape-scale` 换算到帧像素）：
  - 文字中心落在某个紧凑轮廓内（宽 ≤ 2.2 高、宽 ≤ 0.4 窗口宽）且文字 ≤ 6 字符、轮廓高 ≤ 80 → `button`；
  - 其余文字 → `label`（**OCR 文字不会产出 `panel` / `menu`**：分类只看「有没有一个能装下它的按钮框」）；
  - 无文字的小方框 → `checkbox`；小圆 / 其余 → `icon`；实心三角 → `caret`。
- **`shapes[].kind` 契约**（`_detect_shapes` / `_shape_kind`，五选一，没有 `rect` / `square`）：
  - `bar`：宽 > 2.2 高且高 ≤ 16 x scale px（细长横条）；
  - `panel`：宽 > 2.2 高且高 > 16 x scale px，或近似正方（边长差 ≤ max(3, 高的 25%)）且包围盒面积 > checkbox 上限、圆度 < 0.85；
  - `checkbox`：近似正方且包围盒面积 ≤ 400 x scale²；
  - `icon`：其余（非正方且非细长，或近似正方且圆度 ≥ 0.85 的超大框）；
  - `caret`：小尺寸实心三角（下拉箭头），见下一条。
  `elements[].kind` 里的 OpenCV 元素只有 `panel` / `checkbox` / `icon` / `caret`：`_shape_element` 把 `bar` 折算成 `panel`。
- **形状几何阈值随显示密度缩放**：`shape_scale(window, frame_scale)` = 每「96-DPI 设计像素」对应的帧像素数，
  由 `dpi_scale / sqrt(fx*fy)` 得出（`frame_scale` 是 `窗口尺寸 / 帧尺寸`）。两路测量互相独立：
  `dpi_scale` 缺失 / 为 0 / NaN / Inf / 非数字时按 1.0（即「当 96 DPI」）代入，**但仍然保留 `frame_scale` 修正**；
  结果夹在 0.25..4.0。`min/max area`、边长、caret 最小边和 bar 高度均按此换算；修正生效时 `notes` 写 `shape-scale: <scale> device pixels per 96-DPI design pixel`。
  丢掉 `frame_scale` 会让 1.5 倍大的帧仍按设计像素阈值过滤，16x16 的勾选框会被读成 `panel`。
- **`caret` 只认实心三角**：填充率 0.40..0.65、凸度（contour area / hull area）≥ 0.85、凸包按 0.05×周长
  简化后正好 3 条边、长宽比 ≤ 2.4、长边 ≥ 8 x scale。只有填充率不够：字形笔画（`v` / `x` / `4` / `w`）
  在 5x5 闭运算后同样填充 0.40..0.65，实测会被误报成 caret（`X`@13px、`v` `4` `X`@20px）。
  凹轮廓（笔画）过不了凸度闸门，菱形 / 六边形过不了「3 条边」。
- **`caret` 与 OCR 的去重方向是「形状 → 文字」**：caret 框有 ≥ 50% 落进某一条 OCR 行的框里，就认定它是
  那行字被量了两遍（OCR 有字形、Canny 有轮廓），不再单独入清单。真箭头在文字旁边或空的下拉框里，
  任何一行文字都不会盖住它一半。这条与「中心点落在行内」（所有形状都适用）是两道独立的闸门。
- **OpenCV 几何推断不是可交互控件**：`source="opencv"` 的元素恒为 `interactive=false`。
  轮廓只能证明“这里有个这个形状的框”，能不能点由模型读完旁边文字后判断；
  `shapes` 原文也保留，便于核对。
- OpenCV 形状：`Canny` + 形态学闭运算 + `findContours`；过滤小噪声；分类见上面「`shapes[].kind` 契约」
 （`bar` / `panel` / `checkbox` / `icon` / `caret`，没有 `rect` / `square`）。
- 健壮性：OCR 引擎缺失 → 仍返回 UIA 场景，并在 `notes` 写明；若一条元素都没拿到，
  `ok=false`，`reason` 给出一句原因，`error_code` 区分到底是哪一层失败：
  - `INVALID_SOURCE` / `INVALID_REGION`：参数不合法（退出码 2）；
  - `INVALID_HWND` / `UIA_EMPTY` / `UIA_UNAVAILABLE` / `SCENE_FILTERED_EMPTY`：地址不对或树里没东西
    （UIA 空树 ≠ 过滤后空场景 ≠ 取帧失败，这三种不再共用一个码；退出码 3）；
  - `OCR_UNAVAILABLE` / `CAPTURE_FAILED`：本机读不了（退出码 4）；
  - `WINDOW_MINIMIZED` / `UNVERIFIED_FRAME`：窗口状态不允许可靠读（退出码 5）。
- 不得抛异常给调用方（包括参数非法与取帧 / crop / 形状异常），失败一律落到 `error_code` + `reason`。

### 3. `openeyes/core/scene_text.py`（负责：opencode-client）

```python
def render_scene(scene: dict[str, Any], *, max_lines: int = 200,
                 max_chars: int = 6000) -> str:
```

- 首行：`窗口 <title> <w>x<h> @(<left>,<top>) 来源=<source> 像素=<method> 遮挡=<是|否> 可信=<是|否> 元素=<n>`
  - `@(left,top)`：窗口在屏幕上的原点（来自 `window.rect`）；没有 `rect` 时省略。
  - `像素=`：取帧途径（`printwindow` / `screen` / `injected`），未取帧时为 `无`。
  - `遮挡=`：只在 `像素=screen` 时输出，取 `capture.occluded`；`可信=` 取 `capture.verified`（未知时不输出）。
- 失败场景第二行：`失败 <error_code>: <reason>`（没有 `reason` 时只输出 `失败 <error_code>`），
  元素行照旧（通常为空）。
- 每元素一行：`[按钮] <text> @(<cx>,<cy>) <score>`；中文类别名映射：
  button=按钮、label=文本、input=输入框、checkbox=复选框、icon=图标、panel=面板、
  menu=菜单、unknown=未知；映射表里没有的 kind（如 OpenCV 的 `caret`）原样输出 `[caret]`，不冒充 `未知`；
  无文字时省略 `<text>`。
- 按 (y, x) 排序；超出 `max_lines` / `max_chars` 截断并留一行省略说明。
- 纯 ASCII 标点 + 中文类别名；不加 ANSI 颜色。

### 4. CLI 与 MCP（负责：Codex 主模型）

- `eyes scene --window <hwnd> [--json] [--format text|json] [--max-items N]
  [--source auto|uia|ocr] [--no-shapes]`：默认打印文字摘要（首行带窗口屏幕原点、像素来源与可信度，
  失败第二行带 `error_code` 与原因），`--json` 输出完整场景（JSON 里带 `text_digest` 字段）。
  `--source uia` 只读无障碍树，不初始化 OCR、不取帧、不出形状；
  视觉特征要显式走 `--source auto/ocr`（`uia` 一个都不读时宁可 `error_code` 判红，也不偷偷换层）。
  退出码：2 参数错、3 那里没东西、4 本机读不了、5 窗口状态不允许。
- MCP 工具 `scene_describe(hwnd, source?, max_items?, include_shapes?)` → 场景 JSON；
  工具说明同步写清「bbox 为窗口相对像素、摘要首行给屏幕原点与 `capture.verified`、
  `uia` 不跑 OCR、失败带 `error_code`+`reason`」。

## 实测（本机，2026-11-14）

| 目标 | 结果 |
| :- | :- |
| Trae CN（无 UIA 内容） | `eyes scene` 读出标题、菜单栏、`SOLO`、文件页签；58 个元素，来源 `mixed` |
| 取帧 | `PrintWindow` 读出被 Codex 窗口盖住的 Trae 真页面；Qt（EverNote）返回空面，自动退回屏幕抓取并注明 |
| 耗时 | CPU RapidOCR 在 1886x1183 上约 15–20 s（与分辨率关系不大，瓶颈是检测模型）；锚点探针仍是 0.15 s 的快路 |
| 飞书（Chromium） | UIA 树很吵（`Pane` / `ContentsView` 之类），按上面的去重与外壳过滤后只留可交互控件与 OCR 文字 |

## 验收

- `python -m pytest -q tests/test_ocr_backend.py tests/test_scene.py tests/test_scene_text.py
  tests/test_dpi.py tests/test_coords.py tests/test_selector_coords.py`
  - 坐标系：`test_uia_elements_keep_the_backend_window_space_and_the_origin_is_reported`
    （窗口在 59,80；首行给出该原点）、`test_a_frame_with_another_pixel_density_is_resampled_into_window_pixels`
    （800x600 窗口 / 1200x900 帧：网格仍是窗口像素，OCR 框被重采样后与 UIA 框重合）、
    `test_a_uia_element_outside_the_window_is_not_an_executable_position`
    （越界丢弃 + 跨边界裁剪）、`test_uia_backend_reports_window_relative_boxes` 与
    `test_selector_click_through_the_real_backend_adds_the_origin_once`（后端减原点、点击加一次）、
    `test_the_uia_backend_refuses_to_guess_a_window_origin`（读不到矩形就报错，不拿 (0,0) 顶替）；
  - 像素可信度：`test_a_screen_grab_that_cannot_be_proven_is_never_read`、
    `test_a_foreground_screen_grab_is_read_and_marked_trustworthy`、
    `test_being_foreground_does_not_make_a_partly_covered_grab_trustworthy`（前台但被置顶窗盖住）、
    `test_the_auto_screen_fallback_refuses_a_covered_window`、
    `test_the_occlusion_probe_accepts_a_window_that_owns_every_sample` /
    `test_the_occlusion_probe_names_the_covering_window` /
    `test_the_occlusion_probe_cannot_run_is_not_clear`（探测失败即不可信）、
    `test_opencv_shapes_are_evidence_not_interactive_controls`；
  - OCR 闸门：`test_source_uia_never_touches_the_ocr_engine`、
    `test_a_blank_printwindow_frame_does_not_send_a_uia_read_to_the_screen`、
    `test_uia_with_shapes_requested_still_captures_nothing`，
    另钉住 `auto` 仍会回退屏幕抓取（`test_auto_still_falls_back_to_the_screen_when_printwindow_carries_no_text`）；
  - 失败可分：`test_an_empty_tree_and_a_filtered_scene_fail_differently`、
    `test_an_unreadable_tree_is_a_structured_failure`、
    `test_a_window_rectangle_without_area_is_not_readable`、
    `test_an_unknown_source_is_a_structured_failure`、`test_a_malformed_region_is_a_structured_failure`、
    `test_a_region_outside_the_frame_is_a_structured_failure`、
    `test_a_crop_exception_does_not_escape_the_scene`；
  - 尺度与形状（第 4 轮）：`test_shape_scale_keeps_the_frame_correction_without_a_dpi_read`
    （dpi 读不到时仍乘 frame 修正：1.5 倍帧 → 1.5）、
    `test_a_bigger_frame_without_a_dpi_read_still_finds_a_checkbox`（无 dpi 读数、帧 1.5 倍，
    24x24 帧像素的方框仍判 `checkbox`）、
    `test_coverage_fallback_without_numpy_matches_the_mask_path`（无 numpy 时走 `ImportError`
    分支，与掩码路径同值、不再 `ValueError`）、
    `test_a_caret_inside_a_text_line_is_the_same_ink_reported_once`（caret 被 OCR 行盖住一半即丢弃）、
    `tests/test_opencv_regressions.py::test_text_glyphs_are_never_read_as_carets`
    （`v` / `x` / `4` / `X` / `V` / `w` / `7` x 4 个字号 全部不得判成 caret）、
    `test_a_solid_triangle_is_still_a_caret_next_to_text`、
    `test_a_small_but_solid_triangle_clears_the_caret_gate`；
  - DPI 签名：`tests/test_dpi.py`（`AreDpiAwarenessContextsEqual` 用完整指针句柄比较；
    restype/argtypes 缺失时旧写法会把 64 位句柄截成 32 位）。
- `python -m ruff check openeyes tests`
- 真机：Trae CN（无 UIA 内容）应读到 `SOLO` / `新任务` / 输入框占位；记事本（有 UIA）
  应走 `uia` 优先且元素带名字。
- 源文件 UTF-8 无 BOM。

## 不做什么

- 不做通用视觉理解：形状只到「面板 / 方框 / 图标 / 条」这一档，语义留给模型。
- 不替模型做判断：输出是证据与定位，不是「该点哪里」的结论。
- 不做后台自动化：取帧优先 PrintWindow，但动作仍受仓库既有前台闸门约束。
