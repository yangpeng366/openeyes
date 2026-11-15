"""OpenCV-focused micro-benchmarks for the synthetic-vision paths.

Measures, on generated images (no screen capture, no OCR, no UIA):

  1. ``anchors.match`` and ``anchors._best_match_score`` timing over 10 runs.
  2. ``scene._detect_shapes`` timing, shape count and kind distribution.
  3. Local-maxima suppression effectiveness: raw above-threshold pixels ->
     dilate peaks (candidates before nms) -> results after nms.  The peak
     counting replicates, with cv2/numpy directly, the exact suppression loop
     inside ``anchors.match`` so the "before nms" number is the real list.

Run from anywhere:  python benchmarks/bench_opencv.py
Output: a single JSON object on stdout.
"""
from __future__ import annotations

import json
import sys
import time
from collections import Counter
from pathlib import Path

import cv2
import numpy as np
import PIL
from PIL import Image, ImageDraw

# Force the imports below to resolve to the source tree this file lives in,
# rather than any editable/pip install that may point at another checkout.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from openeyes.core import scene  # noqa: E402
from openeyes.core.anchors import DEFAULT_SCALES, _best_match_score, match, nms  # noqa: E402

RUNS = 10
THRESHOLD = 0.80
NMS_IOU = 0.3
SCALES = DEFAULT_SCALES


# --------------------------------------------------------------------------- #
# timing helpers
# --------------------------------------------------------------------------- #
def _stats(times_ms: list[float]) -> dict[str, float]:
    return {
        "min_ms": round(min(times_ms), 3),
        "avg_ms": round(sum(times_ms) / len(times_ms), 3),
        "max_ms": round(max(times_ms), 3),
    }


def _timed(fn, runs: int = RUNS):
    """Run ``fn`` ``runs`` times; return (min/avg/max ms, last result)."""
    times: list[float] = []
    result = None
    for _ in range(runs):
        start = time.perf_counter()
        result = fn()
        times.append((time.perf_counter() - start) * 1000.0)
    return _stats(times), result


def _jsonable(obj):
    """Recursively convert numpy scalars / non-finite floats to strict JSON."""
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        obj = float(obj)
    if isinstance(obj, float):
        return obj if np.isfinite(obj) else None
    if isinstance(obj, (str, int, bool)) or obj is None:
        return obj
    return str(obj)


# --------------------------------------------------------------------------- #
# synthetic fixtures (PIL.Image / PIL.ImageDraw + numpy / cv2)
# --------------------------------------------------------------------------- #
def make_template_image():
    """1920x1080 frame with one known template pasted at 3 locations."""
    width, height = 1920, 1080
    rng = np.random.default_rng(20261115)

    # Light, faintly textured background (numpy directly).
    noise = rng.normal(0.0, 5.0, (height, width)).astype(np.float32)
    gray = np.clip(212.0 + noise, 0, 255).astype(np.uint8)
    frame = Image.fromarray(np.repeat(gray[:, :, None], 3, axis=2), "RGB")

    # Very-low-contrast background furniture (PIL.ImageDraw): enough to be
    # "scene-like" but too faint to correlate with the template.
    draw = ImageDraw.Draw(frame)
    for left, top, right, bottom in [
        (120, 140, 520, 300), (900, 700, 1400, 920), (1500, 120, 1840, 420)
    ]:
        draw.rectangle([left, top, right, bottom], outline=(205, 205, 205), width=2)

    # Smooth, low-frequency template (a Gaussian blob with a gentle tilt).
    # A smooth template keeps TM_CCOEFF_NORMED high over a BROAD neighbourhood
    # of shifts and across neighbouring scales, which is what gives the
    # dilate local-maxima suppression and nms real work to do in section 3.
    # High-frequency marker shapes would instead make the peak one pixel wide
    # and leave nothing to suppress, so they are deliberately avoided here.
    tw, th = 64, 64
    yy, xx = np.mgrid[0:th, 0:tw]
    cx, cy = (tw - 1) / 2.0, (th - 1) / 2.0
    sigma = 13.0
    blob = 150.0 * np.exp(-((xx - cx) ** 2 + (yy - cy) ** 2) / (2.0 * sigma ** 2))
    tilt = 15.0 * xx / tw + 10.0 * yy / th  # low-frequency, gives a unique peak
    gray_tpl = np.clip(45.0 + blob + tilt, 0, 255).astype(np.uint8)
    template = Image.fromarray(np.repeat(gray_tpl[:, :, None], 3, axis=2), "RGB")

    locations = [(200, 180), (760, 520), (1320, 820)]
    for x, y in locations:
        frame.paste(template, (x, y))

    return frame, template, locations


def make_shape_image():
    """640x480 frame with rectangles, circles and triangles drawn on it."""
    width, height = 640, 480
    img = Image.new("RGB", (width, height), (232, 232, 232))
    draw = ImageDraw.Draw(img)
    ink = (35, 35, 35)
    outline = (10, 10, 10)
    drawn: Counter = Counter()

    def add_rect(box, label):
        draw.rectangle(box, fill=ink, outline=outline)
        drawn[label] += 1

    def add_circle(cx, cy, r):
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=ink, outline=outline)
        drawn["circle"] += 1

    def add_triangle(points):
        draw.polygon(points, fill=ink)
        drawn["triangle"] += 1

    # Thin horizontal bars -> "bar" (w > 2.2*h and h <= 16).
    for top in (40, 70, 100):
        add_rect([40, top, 160, top + 10], "thin_bar")
    # Wide tall rectangles -> "panel" (w > 2.2*h and h > 16).
    for left, top in ((220, 40), (220, 110), (220, 180)):
        add_rect([left, top, left + 160, top + 44], "wide_panel")
    # Small squares -> "checkbox" (square-ish, area <= 400).
    for left, top in ((430, 40), (470, 40), (430, 90)):
        add_rect([left, top, left + 16, top + 16], "small_square")
    # Circles -> "icon" (square-ish, area > 400, circularity >= 0.85).
    for cx, cy, r in ((60, 250, 22), (150, 250, 22), (60, 350, 30)):
        add_circle(cx, cy, r)
    # Triangles -> intended "caret" (3 vertices); the solidity gate filters them.
    for bx, by, w, h in ((250, 270, 60, 50), (350, 270, 60, 50),
                         (470, 270, 60, 50), (250, 370, 70, 55),
                         (370, 370, 70, 55)):
        add_triangle([(bx, by + h), (bx + w // 2, by), (bx + w, by + h)])
    return img, dict(drawn)


# --------------------------------------------------------------------------- #
# 3. local-maxima suppression effectiveness
# --------------------------------------------------------------------------- #
def suppression_report(image, template):
    """Rebuild ``match``'s candidate list with cv2/numpy and measure each stage.

    Stages (all summed over the same scale sweep ``match`` uses):
      * raw_above_threshold_pixels - every pixel >= threshold (the naive input
        to nms if local-maxima suppression did not run);
      * candidates_before_nms     - dilate local-maxima peaks (the real list
        ``match`` hands to ``nms``);
      * candidates_after_nms      - what ``nms`` keeps (final matches).
    """
    source_rgb = np.asarray(image.convert("RGB"))
    target_rgb = np.asarray(template.convert("RGB"))
    source = cv2.cvtColor(source_rgb, cv2.COLOR_RGB2GRAY)
    target = cv2.cvtColor(target_rgb, cv2.COLOR_RGB2GRAY)
    image_h, image_w = source.shape[:2]
    template_h, template_w = target.shape[:2]

    raw_above = 0
    candidates: list[dict] = []
    for scale in SCALES:
        w = max(1, int(round(template_w * scale)))
        h = max(1, int(round(template_h * scale)))
        if w > image_w or h > image_h:
            continue
        interpolation = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
        resized = cv2.resize(target, (w, h), interpolation=interpolation)
        result = cv2.matchTemplate(source, resized, cv2.TM_CCOEFF_NORMED)
        result_clean = np.nan_to_num(result, nan=0.0)
        raw_above += int((result_clean >= float(THRESHOLD)).sum())
        dilated = cv2.dilate(result_clean, np.ones((3, 3), dtype=np.float32))
        peaks = ((result_clean == dilated)
                 & (result_clean >= float(THRESHOLD)))
        ys, xs = np.where(peaks)
        for y, x in zip(ys.tolist(), xs.tolist(), strict=True):
            candidates.append({
                "x": int(x), "y": int(y), "w": int(w), "h": int(h),
                "score": float(result[y, x]),
                "scale": round(float(scale), 2),
            })

    before = len(candidates)
    after = len(nms(candidates, iou_threshold=NMS_IOU))
    match_returned = len(match(image, template, threshold=THRESHOLD,
                               scales=SCALES, nms_iou=NMS_IOU))

    return {
        "scales": list(SCALES),
        "scale_count": len(SCALES),
        "threshold": THRESHOLD,
        "nms_iou": NMS_IOU,
        "raw_above_threshold_pixels": raw_above,
        "candidates_before_nms_dilate_peaks": before,
        "candidates_after_nms": after,
        "match_returned": match_returned,
        "stages": {
            "peak_suppression": {
                "removed": raw_above - before,
                "kept_pct": round(100.0 * before / raw_above, 2) if raw_above else None,
                "collapse_factor_x": round(raw_above / before, 2) if before else None,
            },
            "nms_stage": {
                "removed": before - after,
                "kept_pct": round(100.0 * after / before, 2) if before else None,
            },
            "overall": {
                "removed": raw_above - after,
                "kept_pct": round(100.0 * after / raw_above, 2) if raw_above else None,
                "reduction_x": round(raw_above / after, 2) if after else None,
            },
        },
        # nms is O(n^2) in the candidate count; peak detection shrinks that n.
        "nms_quadratic_work_saved_x": round((raw_above / before) ** 2, 1) if before else None,
    }


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main():
    frame, template, locations = make_template_image()
    shape_img, drawn = make_shape_image()

    # 1. template matching -------------------------------------------------- #
    match_stats, match_hits = _timed(
        lambda: match(frame, template, threshold=THRESHOLD,
                      scales=SCALES, nms_iou=NMS_IOU))
    best_stats, best_val = _timed(
        lambda: _best_match_score(frame, template, scales=SCALES))
    best_default_stats, best_default_val = _timed(
        lambda: _best_match_score(frame, template))

    # 2. shape detection ---------------------------------------------------- #
    shape_stats, shapes = _timed(lambda: scene._detect_shapes(shape_img))
    kinds = Counter(str(s.get("kind", "unknown")) for s in shapes)
    requested_kinds = ("caret", "checkbox", "icon", "bar", "panel")
    distribution = {k: int(kinds.get(k, 0)) for k in requested_kinds}
    distribution["other"] = int(len(shapes) - sum(distribution.values()))
    triangle_note = (
        f"Filled triangles have solidity ~0.50, below "
        f"scene.SHAPE_MIN_SOLIDITY={scene.SHAPE_MIN_SOLIDITY}, so the contour "
        f"pass filters them and caret is 0."
        if distribution["caret"] == 0
        else "carets came from 3-vertex contours."
    )

    # 3. suppression -------------------------------------------------------- #
    suppression = suppression_report(frame, template)

    report = {
        "environment": {
            "python": sys.version.split()[0],
            "cv2": cv2.__version__,
            "numpy": np.__version__,
            "pillow": PIL.__version__,
        },
        "template_matching": {
            "image_size": [frame.width, frame.height],
            "template_size": [template.width, template.height],
            "embedded_locations": len(locations),
            "location_xy": [list(loc) for loc in locations],
            "threshold": THRESHOLD,
            "scales": list(SCALES),
            "scale_count": len(SCALES),
            "runs_per_function": RUNS,
            "match": {
                **match_stats,
                "matches_found": len(match_hits),
                "match_scores": [round(float(h["score"]), 4) for h in match_hits],
            },
            "best_match_score_same_scales": {
                **best_stats,
                "scale_count": len(SCALES),
                "best_score": round(float(best_val), 4),
            },
            "best_match_score_default_1_scale": {
                **best_default_stats,
                "scale_count": 1,
                "best_score": round(float(best_default_val), 4),
            },
        },
        "shape_detection": {
            "image_size": [shape_img.width, shape_img.height],
            "runs": RUNS,
            "timing": shape_stats,
            "shapes_detected": len(shapes),
            "kind_distribution": distribution,
            "drawn_shapes": drawn,
            "triangle_note": triangle_note,
        },
        "nms_effectiveness": suppression,
    }

    print(json.dumps(_jsonable(report), indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
