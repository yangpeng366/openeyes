"""Template anchors for pixel-stable UI targets.

This module is deliberately platform-neutral and has no Windows import. OpenCV
is optional: importing :mod:`openeyes.core.anchors` costs nothing, and only
template matching requires the ``anchors`` extra.

The design keeps three decisions separate:

* :func:`match` finds candidate rectangles in image pixels.
* :func:`classify` turns a score into a strong / weak / miss tier.
* :func:`decide` refuses an ambiguous scene instead of guessing.
"""
from __future__ import annotations

import os
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

ANCHOR_EXTRA = 'pip install "openeyes[anchors]"'
DEFAULT_SCALES = tuple(round(0.80 + index * 0.05, 2) for index in range(10))
SCALE_PRIOR_TOLERANCE = 0.10
SCALE_PRIOR_STEP = 0.02
FRAME_STABLE_MAX_DIFF = 2.0
FRAME_STABLE_MIN_SCORE = 0.92
AMBIGUITY_MARGIN = 0.06
# Same control seen at two neighbouring scales keeps its aspect and grows a
# few pixels. A 1.25 scale step plus integer rounding reaches ~1.32 on small
# templates, so the chain limit is 1.35; a 1.4x box is a different instance
# and stays separate.
CROSS_SCALE_SIZE_RATIO = 1.35
CROSS_SCALE_SIZE_RATIO = 1.35
# Centre tolerance across scale steps, as a fraction of the smaller box.
CROSS_SCALE_CENTRE_FRACTION = 0.25


class AnchorUnavailable(RuntimeError):
    """Raised when the optional OpenCV dependency is not installed."""


def scale_scan(expected_ratio: float | None = None) -> tuple[list[float], dict[str, Any]]:
    """Build the scale sweep and its audit record.

    A known current/template scale-product ratio narrows the sweep to a
    fine-grained band around the expectation. Without a usable prior the
    public fallback sweep is retained unchanged.
    """
    if expected_ratio is not None:
        expected = float(expected_ratio)
        if expected > 0 and np.isfinite(expected):
            low = expected * (1.0 - SCALE_PRIOR_TOLERANCE)
            high = expected * (1.0 + SCALE_PRIOR_TOLERANCE)
            steps = max(1, int(np.floor((high - low) / SCALE_PRIOR_STEP)) + 1)
            values = [low + index * SCALE_PRIOR_STEP for index in range(steps + 1)]
            values.append(expected)
            scales = sorted({round(value, 4) for value in values
                            if value > 0 and value <= high + 1e-9})
            return scales, {
                "mode": "scale-prior",
                "expected_ratio": round(expected, 4),
                "range": [round(low, 4), round(high, 4)],
                "step": SCALE_PRIOR_STEP,
                "scale_count": len(scales),
            }
    return list(DEFAULT_SCALES), {
        "mode": "fallback",
        "expected_ratio": None,
        "range": [DEFAULT_SCALES[0], DEFAULT_SCALES[-1]],
        "step": 0.05,
        "scale_count": len(DEFAULT_SCALES),
    }


def _cv2():
    try:
        import cv2
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise AnchorUnavailable(
            f"OpenCV is required for template anchors; install it with {ANCHOR_EXTRA}"
        ) from exc
    return cv2


def _as_rgb_array(image: Any) -> np.ndarray:
    """Accept a path, PIL image, or array and return an RGB uint8 array."""
    if isinstance(image, (str, os.PathLike)):
        path = Path(image)
        if not path.is_file():
            raise FileNotFoundError(f"anchor image does not exist: {path}")
        with Image.open(path) as opened:
            return np.asarray(opened.convert("RGB"))
    if isinstance(image, Image.Image):
        return np.asarray(image.convert("RGB"))
    array = np.asarray(image)
    if array.size == 0:
        raise ValueError("anchor image is empty")
    if array.ndim == 2:
        array = np.repeat(array[:, :, None], 3, axis=2)
    elif array.ndim == 3 and array.shape[2] == 4:
        array = array[:, :, :3]
    elif array.ndim != 3 or array.shape[2] != 3:
        raise ValueError(f"unsupported anchor image shape: {array.shape}")
    if array.dtype != np.uint8:
        array = np.clip(array, 0, 255).astype(np.uint8)
    return array


def _gray(image: Any) -> np.ndarray:
    cv2 = _cv2()
    rgb = _as_rgb_array(image)
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32)


def _scale_values(scales: Sequence[float] | float) -> list[float]:
    values = ([float(scales)] if isinstance(scales, (int, float))
              else [float(value) for value in scales])
    if not values or any(value <= 0 for value in values):
        raise ValueError("scales must contain positive values")
    return values


def _iou(first: dict[str, Any], second: dict[str, Any]) -> float:
    ax1, ay1 = int(first["x"]), int(first["y"])
    ax2, ay2 = ax1 + int(first["w"]), ay1 + int(first["h"])
    bx1, by1 = int(second["x"]), int(second["y"])
    bx2, by2 = bx1 + int(second["w"]), by1 + int(second["h"])
    inter_w = max(0, min(ax2, bx2) - max(ax1, bx1))
    inter_h = max(0, min(ay2, by2) - max(ay1, by1))
    intersection = inter_w * inter_h
    if not intersection:
        return 0.0
    area_a = max(0, ax2 - ax1) * max(0, ay2 - ay1)
    area_b = max(0, bx2 - bx1) * max(0, by2 - by1)
    union = area_a + area_b - intersection
    return intersection / union if union else 0.0


def _box_center(box: dict[str, Any]) -> tuple[float, float]:
    return (int(box["x"]) + int(box["w"]) / 2.0,
            int(box["y"]) + int(box["h"]) / 2.0)


def _same_instance(first: dict[str, Any], second: dict[str, Any],
                   iou_threshold: float = 0.3) -> bool:
    """Do two matches describe the same physical control?

    At one scale step both boxes are the same template, so overlap (IoU) is
    the right test. Across neighbouring scale steps one control only grows or
    shrinks a few pixels, which drops IoU below the threshold even when the
    centres coincide; there a centre-proximity plus size-ratio rule applies.
    A differently sized box at the same centre, or a neighbour that merely
    touches, stays a separate instance.
    """
    first_scale, second_scale = first.get("scale"), second.get("scale")
    same_scale = (first_scale is not None and second_scale is not None
                  and round(float(first_scale), 2) == round(float(second_scale), 2))
    if first_scale is None or second_scale is None or same_scale:
        return _iou(first, second) > iou_threshold

    widths = [int(first["w"]), int(second["w"])]
    heights = [int(first["h"]), int(second["h"])]
    if min(widths) <= 0 or min(heights) <= 0:
        return False
    (ax, ay), (bx, by) = _box_center(first), _box_center(second)
    # Genuine scale duplicates sit on the same pixels (1-2 px apart). A padded
    # crop around two neighbouring icons is ~18 px apart on a 40 px box, which a
    # half-box window would happily merge into one click target.
    if (abs(ax - bx) > CROSS_SCALE_CENTRE_FRACTION * min(widths)
            or abs(ay - by) > CROSS_SCALE_CENTRE_FRACTION * min(heights)):
        return False
    if (max(widths) / min(widths) > CROSS_SCALE_SIZE_RATIO
            or max(heights) / min(heights) > CROSS_SCALE_SIZE_RATIO):
        return False
    return _iou(first, second) > 0.0


def nms(hits: Iterable[dict[str, Any]], iou_threshold: float = 0.3) -> list[dict[str, Any]]:
    """Keep the best hit from every cluster of the same instance.

    Hits are sorted by score first, then any later hit that describes an
    instance already kept is suppressed -- by IoU inside one scale step and
    by centre/size proximity across steps (see :func:`_same_instance`). The
    public helper is useful for callers that combine match results from
    several templates before making a decision.
    """
    ranked = sorted(
        (dict(hit) for hit in hits),
        key=lambda hit: float(hit.get("score", 0.0)),
        reverse=True,
    )
    # Union-find rather than "compare with what survived": one control can
    # chain 0.80 -> 1.00 -> 1.25, where the ends are 1.56x apart and no
    # pairwise rule would merge them directly. Connected components keep the
    # whole chain as one instance and still report the best score.
    parent = list(range(len(ranked)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    for later in range(len(ranked)):
        for earlier in range(later):
            if not _same_instance(ranked[later], ranked[earlier], iou_threshold):
                continue
            left, right = find(later), find(earlier)
            if left != right:
                parent[left] = right

    kept: list[dict[str, Any]] = []
    seen: set[int] = set()
    for index, hit in enumerate(ranked):
        root = find(index)
        if root in seen:
            continue
        seen.add(root)
        kept.append(hit)
    return kept


def match(
    image: Any,
    template: Any,
    threshold: float = 0.80,
    scales: Sequence[float] | float = DEFAULT_SCALES,
    nms_iou: float = 0.3,
) -> list[dict[str, Any]]:
    """Find ``template`` in ``image`` across a small scale sweep.

    Both inputs may be a path, a PIL image, or an array. Matching is done on
    grayscale with ``TM_CCOEFF_NORMED``. Returned coordinates and sizes are in
    image pixels, and each hit has exactly ``x / y / w / h / score / scale``.
    """
    cv2 = _cv2()
    source = _gray(image)
    target = _gray(template)
    if source.size == 0 or target.size == 0:
        raise ValueError("image and template must both be non-empty")
    if source.ndim != 2 or target.ndim != 2:
        raise ValueError("image and template must be two-dimensional after grayscale")
    if float(np.std(target)) < 1.0:
        raise ValueError("template variance is too low for normalized correlation")

    image_h, image_w = source.shape[:2]
    template_h, template_w = target.shape[:2]
    candidates: list[dict[str, Any]] = []
    for scale in _scale_values(scales):
        width = max(1, int(round(template_w * scale)))
        height = max(1, int(round(template_h * scale)))
        if width > image_w or height > image_h:
            continue
        interpolation = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
        resized = cv2.resize(target, (width, height), interpolation=interpolation)
        result = cv2.matchTemplate(source, resized, cv2.TM_CCOEFF_NORMED)
        ys, xs = np.where(np.isfinite(result) & (result >= float(threshold)))
        for y, x in zip(ys.tolist(), xs.tolist(), strict=True):
            candidates.append({
                "x": int(x),
                "y": int(y),
                "w": int(width),
                "h": int(height),
                "score": float(result[y, x]),
                "scale": round(float(scale), 2),
            })
    return nms(candidates, iou_threshold=nms_iou)


def _best_match_score(
    image: Any,
    template: Any,
    scales: Sequence[float] | None = None,
) -> float:
    """Return the strongest finite normalized correlation across scales.

    Frame stability only needs the peak score, so this avoids materializing
    every threshold-0 candidate and running NMS over a full window.
    """
    cv2 = _cv2()
    source = _gray(image)
    target = _gray(template)
    if float(np.std(target)) < 1.0:
        raise ValueError("template variance is too low for normalized correlation")
    best = 0.0
    for scale in _scale_values(scales or [1.0]):
        width = max(1, int(round(target.shape[1] * scale)))
        height = max(1, int(round(target.shape[0] * scale)))
        if width > source.shape[1] or height > source.shape[0]:
            continue
        interpolation = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
        resized = cv2.resize(target, (width, height), interpolation=interpolation)
        result = cv2.matchTemplate(source, resized, cv2.TM_CCOEFF_NORMED)
        finite = result[np.isfinite(result)]
        if finite.size:
            best = max(best, float(np.max(finite)))
    return best


def assess_frames(
    frames: Sequence[Any],
    template: Any | None = None,
    *,
    max_abs_diff: float = FRAME_STABLE_MAX_DIFF,
    min_score: float = FRAME_STABLE_MIN_SCORE,
    scales: Sequence[float] | None = None,
) -> dict[str, Any]:
    """Measure consecutive-frame drift and, optionally, template agreement.

    ``max_abs_diff`` is the largest raw RGB channel difference between
    consecutive captures. ``min_score`` is the weakest whole-window template
    match across the supplied frames. Both gates must pass.

    ``template=None`` means pixel stability only. That is the correct mode for
    checking an already-matched anchor crop: a template that belongs to a
    different window is an anchor lookup miss, not evidence that the screen
    is unstable.
    """
    arrays = [_as_rgb_array(frame) for frame in frames]
    pixel_reasons: list[str] = []
    if len(arrays) < 2:
        pixel_reasons.append("too-few-frames")
    max_diff = 0.0
    if arrays and any(array.shape != arrays[0].shape for array in arrays[1:]):
        pixel_reasons.append("size-changed")
    else:
        for previous, current in zip(arrays, arrays[1:], strict=False):
            diff = np.abs(current.astype(np.int16) - previous.astype(np.int16))
            max_diff = max(max_diff, float(diff.max(initial=0)))
    if max_diff > float(max_abs_diff):
        pixel_reasons.append("pixel-diff")

    scores: list[float] = []
    weakest: float | None = None
    template_stable = True
    if template is not None:
        for frame in arrays:
            scores.append(_best_match_score(frame, template, scales))
        weakest = min(scores, default=0.0)
        template_stable = weakest >= float(min_score)

    reasons = list(pixel_reasons)
    if not template_stable:
        reasons.append("template-score")
    return {
        "stable": not reasons,
        "pixel_stable": not pixel_reasons,
        "template_stable": template_stable,
        "max_abs_diff": max_diff,
        "min_score": weakest,
        "frames": len(arrays),
        "frame_scores": scores,
        "reasons": reasons,
    }



def classify(score: float, strong: float = 0.92, weak: float = 0.80) -> str:
    """Return ``strong``, ``weak`` or ``miss`` for a template score."""
    if strong < weak:
        raise ValueError("strong threshold must be greater than or equal to weak")
    value = float(score)
    if value >= strong:
        return "strong"
    if value >= weak:
        return "weak"
    return "miss"


def decide(
    hits: Sequence[dict[str, Any]],
    require_unique: bool = True,
    weak_support: bool = False,
    margin: float = AMBIGUITY_MARGIN,
) -> dict[str, Any]:
    """Choose a hit, or refuse when the scene is ambiguous.

    Every hit at or above the weak floor competes, not only those in the
    winner's tier: 0.95 next to 0.90 is one scene seen twice, so a tier
    boundary must not launder that into "unique". A rival counts only when it
    is within ``margin`` of the winner - beyond that the winner is separated
    by evidence rather than by the tier label.

    With ``require_unique`` (the default), a rival inside the margin produces
    ``chosen=None`` and ``ambiguous=True``; when uniqueness is not required,
    the winner is returned while ``ambiguous`` still records the competition.
    A weak winner is eligible only when ``weak_support`` is true.
    """
    if margin < 0:
        raise ValueError("margin must be non-negative")
    ranked = sorted(
        (dict(hit) for hit in hits if classify(hit.get("score", 0.0)) != "miss"),
        key=lambda hit: float(hit.get("score", 0.0)),
        reverse=True,
    )
    if not ranked:
        return {
            "chosen": None,
            "strength": "miss",
            "ambiguous": False,
            "reason": "no match at or above the weak threshold",
        }

    winner = ranked[0]
    strength = classify(winner.get("score", 0.0))
    top = float(winner.get("score", 0.0))
    rivals = [hit for hit in ranked[1:]
              if top - float(hit.get("score", 0.0)) <= margin + 1e-9]
    if strength == "weak" and not weak_support:
        # A weak winner still competes: two weak rivals are one ambiguous
        # scene, not a plain weak refusal. Callers that only read `ambiguous`
        # must not be told the scene was clean.
        return {
            "chosen": None,
            "strength": "weak",
            "ambiguous": bool(rivals),
            "reason": (
                f"weak match next to {len(rivals)} rival(s) within {margin:.2f}; "
                "refusing without weak_support"
                if rivals else
                "weak match rejected because weak_support is disabled"),
        }
    if not rivals:
        return {
            "chosen": winner,
            "strength": strength,
            "ambiguous": False,
            "reason": f"unique {strength} anchor",
        }
    if require_unique:
        return {
            "chosen": None,
            "strength": strength,
            "ambiguous": True,
            "reason": (f"multiple anchors within {margin:.2f} of the best "
                       f"{strength} hit; refusing to guess"),
        }
    return {
        "chosen": winner,
        "strength": strength,
        "ambiguous": True,
        "reason": (f"best of {len(rivals) + 1} anchors within {margin:.2f}; "
                   f"uniqueness not required"),
    }
