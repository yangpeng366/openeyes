"""合成图测试：多尺度模板匹配 + NMS + 置信分档。

所有输入都由 numpy 现场生成，不依赖任何真实截图或外部素材。
被测模块 ``openeyes.core.anchors`` 若尚未实现，本文件会在 import 阶段
如实报 ModuleNotFoundError —— 这是红灯，不是静默跳过。
"""
from __future__ import annotations

import numpy as np
import pytest

# 被测模块必须真实存在；缺失时让 import 直接失败，避免用空实现掩盖问题。
from openeyes.core.anchors import classify, decide, match

# cv2 缺失时没有模板匹配能力，明确 skip 并写明原因，绝不静默通过。
cv2 = pytest.importorskip(
    "cv2",
    reason="缺少 opencv-python，合成图模板匹配无法运行",
)

TEMPLATE_SIZE = 20


def _make_template(seed: int = 7) -> np.ndarray:
    """生成 20x20 高频随机灰度块，便于匹配时形成尖锐峰值。"""
    rng = np.random.default_rng(seed)
    return rng.integers(0, 256, size=(TEMPLATE_SIZE, TEMPLATE_SIZE), dtype=np.uint8)


def _make_canvas(
    height: int = 180,
    width: int = 220,
    seed: int = 1,
) -> np.ndarray:
    """生成低噪声灰度底图，降低随机背景带来的伪峰值。"""
    rng = np.random.default_rng(seed)
    return rng.integers(24, 40, size=(height, width), dtype=np.uint8)


def _paste(image: np.ndarray, template: np.ndarray, x: int, y: int) -> np.ndarray:
    """把模板贴到画布指定左上角。"""
    image[y : y + template.shape[0], x : x + template.shape[1]] = template
    return image


def _center(hit: dict) -> tuple[float, float]:
    return hit["x"] + hit["w"] / 2.0, hit["y"] + hit["h"] / 2.0


def _iou(a: dict, b: dict) -> float:
    """计算两个轴对齐框的 IoU，用于校验 NMS 后不再有重叠框。"""
    ax1, ay1 = a["x"], a["y"]
    ax2, ay2 = ax1 + a["w"], ay1 + a["h"]
    bx1, by1 = b["x"], b["y"]
    bx2, by2 = bx1 + b["w"], by1 + b["h"]

    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    inter_w = max(0, ix2 - ix1)
    inter_h = max(0, iy2 - iy1)
    inter = inter_w * inter_h

    union = a["w"] * a["h"] + b["w"] * b["h"] - inter
    return inter / union if union > 0 else 0.0


def test_exact_position_hit() -> None:
    """模板原尺寸精确嵌入时，应在目标位置命中且 scale 接近 1.0。"""
    template = _make_template(seed=7)
    image = _make_canvas(seed=1)
    x, y = 60, 40
    _paste(image, template, x, y)

    hits = match(image, template, threshold=0.85)

    assert hits, "精确嵌入的模板应当被命中"
    best = max(hits, key=lambda h: h["score"])
    assert best["score"] >= 0.85
    assert best["score"] <= 1.0 + 1e-6

    cx, cy = _center(best)
    assert abs(cx - (x + TEMPLATE_SIZE / 2.0)) <= TEMPLATE_SIZE / 2.0
    assert abs(cy - (y + TEMPLATE_SIZE / 2.0)) <= TEMPLATE_SIZE / 2.0
    assert best["scale"] == pytest.approx(1.0, abs=0.1)


def test_upscaled_image_still_hits() -> None:
    """整图放大 1.2 倍后仍能命中，返回的模板尺度应为 1.2。"""
    template = _make_template(seed=11)
    base = _make_canvas(height=160, width=200, seed=3)
    x, y = 40, 50
    _paste(base, template, x, y)

    image = cv2.resize(base, None, fx=1.2, fy=1.2, interpolation=cv2.INTER_LINEAR)
    scales = [float(s) for s in np.linspace(0.75, 1.5, 16)]

    hits = match(image, template, threshold=0.7, scales=scales)

    assert hits, "整图放大 1.2 倍后仍应命中"
    best = max(hits, key=lambda h: h["score"])

    # 约定：返回的 scale 以模板原始尺寸为 1.0 基准。
    assert best["scale"] == pytest.approx(1.2, abs=0.15)

    # 坐标随整图同样放大 1.2 倍，用半个模板的容差校验位置。
    cx, cy = _center(best)
    assert abs(cx - (x + TEMPLATE_SIZE / 2.0) * 1.2) <= TEMPLATE_SIZE * 1.2 / 2.0
    assert abs(cy - (y + TEMPLATE_SIZE / 2.0) * 1.2) <= TEMPLATE_SIZE * 1.2 / 2.0


def test_two_instances_are_ambiguous() -> None:
    """两个相同实例同时出现时，decide 应判为 ambiguous 且不给唯一选择。"""
    template = _make_template(seed=21)
    image = _make_canvas(height=240, width=320, seed=5)
    _paste(image, template, 30, 40)
    _paste(image, template, 200, 150)

    hits = match(image, template, threshold=0.85)
    strong_hits = [h for h in hits if h["score"] >= 0.85]
    assert len(strong_hits) >= 2, "应至少检出两个相同实例"

    result = decide(hits, require_unique=True, weak_support=False)

    assert result["ambiguous"] is True
    assert result["chosen"] is None
    assert result["reason"], "ambiguous 时必须给出可解释的原因"


def test_below_weak_is_miss() -> None:
    """低于 weak 阈值的分数必须归为 miss。"""
    assert classify(0.50) == "miss"
    # 边界：weak 阈值以下（默认 0.80）一律 miss。
    assert classify(0.79) == "miss"


def test_nms_merges_overlapping_boxes() -> None:
    """多尺度在同一位置产生重叠框时，NMS 应合并为一个。"""
    template = _make_template(seed=31)
    image = _make_canvas(height=180, width=220, seed=9)
    x, y = 70, 60
    _paste(image, template, x, y)

    hits = match(
        image,
        template,
        threshold=0.6,
        scales=[0.9, 1.0, 1.1],
        nms_iou=0.3,
    )

    assert hits, "单一实例至少应有一个命中"
    # 同一目标的多个尺度框彼此重叠，NMS 后只应留下一个。
    assert len(hits) == 1, f"NMS 后应仅剩一个框，实际 {len(hits)} 个"

    # 不变量：NMS 后任意两框 IoU 都不得超过阈值。
    for i in range(len(hits)):
        for j in range(i + 1, len(hits)):
            assert _iou(hits[i], hits[j]) <= 0.3 + 1e-6
