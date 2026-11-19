# 锚点抓帧预算与单尺度快路径（1.3021 实测量化）

> 结论先行：**单次抓帧已落地**（一次 `find_anchor` 只抓一帧，N 个候选模板共用）；
> **单尺度快路径"只跑 [1.0]"不可行**，只能做"先试 [1.0]、分数不足再退 11 尺度带"的两段式，
> 且触发条件必须是当前 `scale_product` 与 entry 的 product 在 4 位小数上**完全相等**。
> 该契约已实现（§3），回执可审计两次尝试。

复现命令：

```powershell
python benchmarks\bench_anchor_frame.py        # 单进程，约 40 s，stdout 为一份 JSON
python -m pytest -q tests\test_anchors.py      # 79 passed（含抓帧预算 3 例 + 快路径 3 例）
```

## 1. 单次抓帧（已落地）

改动 `openeyes/backends/anchor.py::find_anchor`：候选循环里第一次需要像素时抓帧，
其余候选（同组 peer、fallback 阶段）复用同一帧；窗口矩形与像素**同帧采集**，因此
`screen_point` 不会因为"抓帧后窗口又动了"而偏移。

回执新增字段（`find_anchor` / `click_anchor` 的 `target` 之外的顶层）：

| 字段 | 语义 |
|---|---|
| `frames_captured` | 本次调用真实抓帧次数：0（模板缺失/抓帧失败）或 1 |
| `frame_reused` | 是否用同一帧判读了多个模板（`scored > 1`） |
| `window_rect` | 与该帧同时读到的窗口矩形 `[l, t, r, b]`，坐标映射的依据 |

抓帧失败时只尝试一次（不再每个候选重试一遍），回执为 `ERROR_CAPTURE_FAILED` +
`frames_captured=0`。`click_anchor` 的残差回验仍会另抓一帧——那是刻意的"点击前一帧"，
不属于候选判读预算。

新增测试（`tests/test_anchors.py`）：

- `test_find_anchor_captures_one_frame_for_every_candidate`：两个模板 → 只有 1 次 `_capture_window`。
- `test_find_anchor_fallback_scores_on_the_same_frame`：group 未命中转 fallback 时，第二帧（全黑）
  从未被消费 —— 分组期与回退期不可能判读在不同帧上。
- `test_find_anchor_capture_failure_is_attempted_once`：抓帧异常只打一次窗口。

## 2. 单尺度快路径量化

### 2.1 单尺度 vs 尺度带（Trae 实测几何 1886x1183，3 次取均值）

| 模板 | 11 尺度带（ratio=1.0） | 单尺度 [1.0] | 10 尺度 fallback | 带/单 | fallback/单 |
|---|---|---|---|---|---|
| 42x34 | 1432.3 ms | 186.2 ms | 1384.2 ms | 7.69x | 7.43x |
| 120x32 | 1663.2 ms | 191.3 ms | 1514.7 ms | 8.70x | 7.92x |

- 单尺度一次 ≈ 186–191 ms，与模板面积基本无关，因为成本由**帧面积 × 结果网格**主导
  （1886x1183 一次 `matchTemplate` 实测 86–114 ms/尺度，含灰度与峰值提取）。
- 因此 `scale_product` 精确命中时，一个锚点可省 **约 1.25–1.47 s**；`--roi-pad` 路径本来就只在
  小 ROI 上扫，收益按面积同比例缩小（48 px pad 下约省几十毫秒，不值得为它改契约）。
- 注意：本机 7.69–8.70x 是"同一台机器、同一几何"的比值；绝对毫秒不要跨机照搬
  （AGENTS.md 记的"三锚点整窗 2.9 s"来自另一台机器、更小的模板）。

### 2.2 尺度漂移容差（决定性数据）

在 640x400 帧上把控件按 `true_ratio` 缩放后嵌入，模板保持 1.0，单尺度 [1.0] 的分数：

| true_ratio | 0.92 | 0.96 | 0.98 | 0.99 | 1.00 | 1.01 | 1.02 | 1.05 | 1.08 |
|---|---|---|---|---|---|---|---|---|---|
| 控件型（块+条，平滑） | 0.897 | 0.945 | 0.960 | 0.992 | 1.000 | 0.992 | 0.960 | 0.948 | 0.900 |
| 文字型（高频笔画） | 0.399 | 0.508 | 0.715 | 0.734 | 1.000 | 0.735 | 0.718 | 0.448 | 0.390 |

- 平滑控件能容忍 ±7%（strong 0.92）～±8%（acceptance 0.80）；
- **文字/图标型只要漂移 1% 就掉到 0.73，直接低于 0.80 验收线**——所以"只跑单尺度"在文字模板上
  等于把命中率交给运气。
- 真实 UI 模板多为文字/图标型，取**严口径**：单尺度只在 ratio 精确等于 1.0 时才允许作为第一发。

### 2.3 product 配对是否落在容差内

| 配对 | ratio | 平滑控件 | 文字模板 |
|---|---|---|---|
| 1.3021 vs 1.3021（同源） | 1.000000 | 命中 | 命中（唯一） |
| 125/96=1.3020833 vs 存 1.3021（4 位舍入） | 0.999987 | 命中 | **掉到 0.73** |
| 1.3 vs 1.3021 | 0.998387 | 命中 | **掉线** |
| dpr1.25×zoom1.0=1.25 vs 1.3021 | 0.959988 | 命中（0.945） | 掉线 |
| 150% DPI 1.5625 vs 1.3021 | 1.199985 | 掉线 | 掉线 |
| dpr1.25×zoom1.1=1.375 vs 1.3021 | 1.055986 | 命中 | 掉线 |

这就是"1.3021 舍入误差"的量化答案：**4 位小数舍入（0.999987）对文字模板已经足够致命**。
所以快路径的触发条件不能写成 `abs(ratio-1) < 0.01`，只能是 `current_product == entry_product`
（同一表达式、同一四舍五入落库后比较），其余一律走 11 尺度带。

## 3. `scan_strategy` 两段式快路径契约（已落地）

- 门禁：`scale_product is not None` 且与所选 entry 的 `scale_product` 在 4 位小数上相等，
  且 product 由调用方显式传入（`auto_scale` 推断值不参与，见 §4）→ 第一发 `scales=[1.0]`。
- 通过条件：`score >= 0.92`（strong）→ 直接用这一发判读。
- 否则**无缝**升级到 11 尺度带（0.80–1.30 / step 0.05，同一帧），总成本 = 单尺度 186 ms
  + 带 1432 ms，比原契约只多一次单尺度尝试；回执写清两次尝试。
- 禁止：把低分单尺度命中直接当结果返回——那会把"可能只是尺度漂移"误报成
  `weak`/`ambiguous`，正是"误报率=0"红线的反面。

落地位置：`openeyes/core/anchors.py::scan_contract`（门禁 + 首段）、`escalate_scan`（band），
`openeyes/backends/anchor.py::_two_stage_match`（两段串联，`find_anchor` 与
`find_anchor_in_image` 共用），`tests/test_anchors.py` 三例（门禁 / 单尺度命中 / 升级命中）。

探针路径同口径（2026-11-18 复核 + 修复）：`probe_anchors` 自己推断 product 时走
`product_explicit=False` → `probe_anchors_in_image` → `find_anchor_in_image`，门禁对推断值关闭，
扫描仍是 11 尺度带（`expected_ratio=1.0`、`range=[0.9, 1.1]`、`step=0.02`、`scale_count=11`）；
`probe_anchors_in_image` 收到显式 product（CLI `--scale-product` / MCP `anchor_probe`）时仍走单尺度
首段。`roi_pad` 只是先把帧裁成「存档矩形 + pad」，命中结果按 `search_region.origin` 平移回全帧坐标，
两段式没有被复写。`roi_pad` + 显式 product 时单尺度首段照旧生效，首段弱于 0.92 时升级也在裁出的
小图上完成（坐标按 `search_region.origin` 平移回全帧）。集成用例：
`test_find_anchor_auto_scale_product_scans_the_eleven_scale_band`、
`test_probe_anchors_auto_scale_product_keeps_the_band`、
`test_probe_roi_pad_reuses_the_two_stage_scan`、
`test_probe_roi_pad_explicit_product_keeps_the_fast_path`、
`test_probe_roi_pad_fast_path_still_escalates_inside_the_crop`。

轮询与缩窗审计（2026-11-18 复核）：

- `wait_anchors` 每次 poll 都只把 `scale_product` 透传给 `probe_anchors`，所以门禁语义与单次探针完全一致：
  显式 product 时**每一帧**都走单尺度首段（证据在 `receipt["last"]["anchors"][name]["scan_strategy"]`，
  `fast_path=true`、`scale_count=1`），只给窗口 DPI 时每一帧仍是 11 尺度带。用例：
  `test_wait_anchors_explicit_product_polls_on_one_scale`、
  `test_wait_anchors_inferred_product_keeps_the_band`。
- `_roi_box` 更名 `_roi_window`，返回 `(box, reason)`；`roi_pad` 存在时回执新增 `receipt["roi"]`
  （每个 name 一项 `pad` / `applied` / `reason` / `region`）。
- 关键取舍：推断 product 下，某个 name 若只在**别的** product 有存档，裁窗会没有矩形可用。
  此时不是裁成空图（那会把屏幕上的控件读成 `missing`），而是退回全帧搜索，并在 `roi.reason` 写明
  `no-stored-rect-at-this-scale`——全帧答案不再能被误读成缩窗答案。其余 reason：`no-app`、
  `store-unreadable`、`no-stored-rect`、`outside-frame`。用例：
  `test_probe_roi_pad_widens_instead_of_missing_a_name_at_another_scale`、
  `test_roi_window_names_why_the_pad_was_dropped`。
- 轮询回执另带顶层汇总 `receipt["roi"]`（`roi_pad` 未传时为 `None`）：`pad`、`applied`（真正被
  缩窗的 name，顺序同 `last["roi"]`）、`widened`（退回全帧的个数）、`reasons`（丢弃理由码计数）。
  判 poll 是否缩窗、为什么没缩，不必再逐 name 读 `last["roi"]`。用例：
  `test_wait_anchors_rolls_the_roi_map_into_one_line`。
- 并集退化的量化边界：同名两组 rect 相距很远时，`applied: true` 只说明矩形存在，不说明省了多少。
  400x300 帧、pad=16、两组 rect 分处左上/右下时，并集被夹成 `[0, 0, 400, 300]`（全帧 120,000 px）；
  单组本应是 74x66 = 4,884 px（帧的 4.07%，省 24.57 倍），并集把收益降到 1.00 倍。答案仍然正确
  （远处那组照样命中、轮询 satisfied），只是不再省时。用例：
  `test_roi_union_of_two_distant_groups_covers_the_whole_frame`。

回执字段（`report["scan_strategy"]`）：

| 字段 | 语义 |
|---|---|
| `fast_path` | 本次候选是否走了单尺度首段（product 4 位相等且显式传入） |
| `fast_path_scales` / `scale_count` | 首段尺度；未升级时为 1，升级后为该带长度 11 |
| `fast_path_score` | 首段最佳分（无命中为 null），升级与否的判据 |
| `escalate_below` | 升级门限 0.92 |
| `escalated` | 是否真的升级到 11 尺度带 |
| `escalation` | 升级带的 `range`/`step`/`scale_count`，便于事后审计 |
| `mode` | 仍然只有 `scale-prior` / `fallback` 两值，快路径不新增 mode |

## 4. 对 `scan_strategy` 契约的建议（历史，已按 §3 实现）

- 门禁：`scale_product is not None` 且与所选 entry 的 `scale_product` 在 4 位小数上相等
  → 第一发 `scales=[1.0]`。
- 通过条件：`score >= 0.92`（strong），且命中唯一（`peaks == 1`）→ `ok`。
- 否则**无缝**回退到 `scale_scan(1.0)` 的 11 尺度带，总成本 = 186 ms + 1432 ms，比现在只多一次
  单尺度尝试；回执必须写清两次尝试：`scan_strategy.mode="scale-prior"`、
  `scan_strategy.fast_path=true`、`fast_path_score`、`escalated=true`。
- 禁止：把 `mode` 改成 "single-scale" 并直接返回低分命中——那会把"可能只是尺度漂移"误报成
  `weak`/`ambiguous`，正是"误报率=0"红线的反面。
- `auto_scale` 推断出的 product 不参与快路径门禁（推断源与落库源不同，比的是不同表达式）。

## 5. 遗留

- 真机复核：本次量化全在合成图上；`--roi-pad 48` + 文字模板的真机打分曲线待虚拟显示适配器可用后补。
- 抓帧本身的开销（PrintWindow 1886x1183）未在本轮测量（无可用桌面），只用回执字段证明次数从 N 降到 1。
- `probe_anchors` 早就是「一帧 N 判」；本轮（2026-11-18）把快路径门禁也对齐了——推断的 DPI
  product 不再冒充显式 product，探针与 `find_anchor` 同走 11 尺度带；轮询路径同口径已有用例钉住。
- 缩窗回退已可审计，但**未量化**：`roi.reason=no-stored-rect-at-this-scale` 时退回全帧的那一帧，
  多花的毫秒数仍未测（需真机桌面）；当前只有「不会误报 missing」的用例证据。
- 仍未证实的一项：`roi_pad` 缩窗后若控件真的挪出 pad，只会在裁片内读成 `missing`（设计取舍，
  不是 bug），真机量化仍需桌面。
