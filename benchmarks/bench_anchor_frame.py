"""Frame-budget + scale-fast-path micro-benchmarks for ``anchor find``.

Two questions, both on generated images (no screen capture):

  1. **Frame budget.**  How many captures does one ``find_anchor`` call need?
     That is receipt data now (``frames_captured`` / ``frame_reused``), so the
     count is asserted in ``tests/test_anchors.py`` where the window layer is
     stubbed.  Here we measure the *matching* term that a reused frame saves:
     N candidate templates scored on one frame cost N matches, but only one
     capture.

  2. **Single-scale fast path at scale_product 1.3021.**  A same-group hit
     implies ``expected_ratio == 1.0``, yet ``scale_scan(1.0)`` still sweeps 11
     scales.  Before short-circuiting to ``[1.0]`` we need the failure curve:
     how far may the true on-screen scale drift from the stored one before a
     one-scale attempt drops below the acceptance / strong thresholds?

Timing note: ``matchTemplate`` cost is linear in frame area x template area, so
the drift sweep runs on a 640x400 frame (ratios are scale-free) while the
band-vs-single cost comparison uses the real Trae geometry 1886x1183.

Run from anywhere:  python benchmarks/bench_anchor_frame.py
Output: one JSON object on stdout.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from openeyes.core.anchors import (  # noqa: E402
    DEFAULT_SCALES,
    _best_match_score,
    match,
    scale_scan,
)

# 1886x1183 @1.3021 is the Trae window size / pixel scale measured on the host
# (AGENTS.md item 9).  Keeping the frame real keeps the timing honest.
FRAME_SIZE = (1886, 1183)
DRIFT_FRAME_SIZE = (640, 400)
RUNS = 3
THRESHOLD = 0.80
STRONG = 0.92


def _template(height: int, width: int, seed: int = 20261117) -> np.ndarray:
    """A UI-control-like crop: ink shape on a light panel, a little texture.

    Pure noise correlates too sharply to be representative (a one-pixel shift
    already loses the match) and a flat panel cannot be matched at all.
    """
    rng = np.random.default_rng(seed)
    base = np.full((height, width), 236, dtype=np.float32)
    yy, xx = np.mgrid[0:height, 0:width]
    block = ((xx - width * 0.28) ** 2 / (width * 0.16) ** 2
             + (yy - height * 0.5) ** 2 / (height * 0.28) ** 2) <= 1.0
    bar = ((xx > width * 0.52) & (xx < width * 0.86)
           & (yy > height * 0.38) & (yy < height * 0.62))
    base[block] = 58.0
    base[bar] = 96.0
    base += rng.normal(0.0, 4.0, (height, width))
    return np.clip(base, 0, 255).astype(np.uint8)


def _frame(template: np.ndarray, size=FRAME_SIZE, seed: int = 7) -> np.ndarray:
    width, height = size
    rng = np.random.default_rng(seed)
    canvas = np.full((height, width), 242, dtype=np.float32)
    canvas += rng.normal(0.0, 3.0, (height, width))
    for left, top, right, bottom in [(60, 80, 420, 300), (600, 120, 980, 420)]:
        left, top = min(left, width - 1), min(top, height - 1)
        right, bottom = min(right, width), min(bottom, height)
        if right > left and bottom > top:
            canvas[top:bottom, left:right] = 226.0
    frame = np.clip(canvas, 0, 255).astype(np.uint8)
    th, tw = template.shape[:2]
    x = max(0, width - tw - 86)
    y = max(0, (height - th) // 2)
    frame[y:y + th, x:x + tw] = template
    return frame


def _glyph_template(height: int = 32, width: int = 120,
                    seed: int = 20261118) -> np.ndarray:
    """A text-like crop: high-frequency strokes, the tighter drift case.

    Text and icon glyphs have edges at a pixel scale, so a scale mismatch
    decorrelates them faster than the smooth block-and-bar control above.
    """
    rng = np.random.default_rng(seed)
    base = np.full((height, width), 250, dtype=np.float32)
    for index in range(6):
        left = 6 + index * 18
        for stroke in range(4):
            x0 = left + stroke * 3
            y0 = 6 + int(rng.integers(0, 4))
            base[y0:height - 6, x0:x0 + 1] = float(rng.integers(20, 60))
        base[6 + index % 3:9 + index % 3, left:left + 9] = 40.0
    base += rng.normal(0.0, 2.0, (height, width))
    return np.clip(base, 0, 255).astype(np.uint8)


def _resize(template: np.ndarray, ratio: float) -> np.ndarray:
    height, width = template.shape[:2]
    size = (max(1, int(round(width * ratio))), max(1, int(round(height * ratio))))
    interpolation = cv2.INTER_AREA if ratio < 1.0 else cv2.INTER_LINEAR
    return cv2.resize(template, size, interpolation=interpolation)


def _best(hits: list[dict]) -> dict | None:
    return max(hits, key=lambda hit: hit["score"]) if hits else None


def _timed(fn, runs: int = RUNS) -> tuple[dict[str, float], object]:
    times: list[float] = []
    result = None
    for _ in range(runs):
        start = time.perf_counter()
        result = fn()
        times.append((time.perf_counter() - start) * 1000.0)
    return {
        "min_ms": round(min(times), 3),
        "avg_ms": round(sum(times) / len(times), 3),
        "max_ms": round(max(times), 3),
    }, result


def sweep_cost() -> dict:
    """Band sweep (11 scales) vs a one-scale attempt at two control sizes."""
    band_scales, band_strategy = scale_scan(1.0)
    rows = []
    for height, width in ((34, 42), (32, 120)):
        template = _template(height, width)
        frame = _frame(template)
        band_stats, band_hits = _timed(
            lambda t=template, f=frame: match(f, t, threshold=THRESHOLD,
                                              scales=band_scales))
        single_stats, single_hits = _timed(
            lambda t=template, f=frame: match(f, t, threshold=THRESHOLD,
                                              scales=[1.0]))
        fallback_stats, _ = _timed(
            lambda t=template, f=frame: match(f, t, threshold=THRESHOLD,
                                              scales=DEFAULT_SCALES))
        band_best = _best(band_hits)
        single_best = _best(single_hits)
        rows.append({
            "template_size": [width, height],
            "band": {**band_stats, "scales": len(band_scales),
                     "best_score": round(band_best["score"], 4) if band_best else None,
                     "best_scale": band_best["scale"] if band_best else None,
                     "hits": len(band_hits)},
            "single_scale_1_0": {
                **single_stats, "scales": 1,
                "best_score": round(single_best["score"], 4) if single_best else None,
                "hits": len(single_hits)},
            "fallback_10": {**fallback_stats, "scales": len(DEFAULT_SCALES)},
            "speedup_band_over_single": (
                round(band_stats["avg_ms"] / single_stats["avg_ms"], 2)
                if single_stats["avg_ms"] else None),
            "speedup_fallback_over_single": (
                round(fallback_stats["avg_ms"] / single_stats["avg_ms"], 2)
                if single_stats["avg_ms"] else None),
        })
    return {
        "frame_size": list(FRAME_SIZE),
        "band_scales_for_ratio_1_0": [round(v, 4) for v in band_scales],
        "band_strategy": band_strategy,
        "runs_per_measurement": RUNS,
        "rows": rows,
    }


def drift_curve(template: np.ndarray, *, label: str, band_probe: bool) -> dict:
    """How far off may the true scale be before a [1.0]-only match fails?

    ``true_ratio`` is current/template scale: the frame embeds the control
    resized by that factor while the stored template stays at 1.0 - exactly the
    drift a fast path faces after a DPI or zoom change the product field did
    not record.  The band sweep is measured on a subset to show it recovers.
    """
    ratios = [round(0.92 + index * 0.01, 2) for index in range(17)]
    probe = {0.92, 0.95, 0.98, 1.0, 1.02, 1.05, 1.08}
    rows = []
    for ratio in ratios:
        frame = _frame(_resize(template, ratio), size=DRIFT_FRAME_SIZE)
        # ``_best_match_score`` is minMaxLoc over one scale: no peak list, so no
        # O(n^2) NMS at a 0.0 threshold.  It is the whole-frame max, i.e. the
        # best score a one-scale attempt could report (a spurious peak would
        # make it look better than it is - the conservative direction).
        single_score = float(_best_match_score(frame, template, scales=[1.0]))
        row = {
            "true_ratio": ratio,
            "single_score": round(single_score, 4),
            "single_accepts_at_0_80": single_score >= THRESHOLD,
            "single_strong_at_0_92": single_score >= STRONG,
            "band_score": None,
            "band_scale": None,
        }
        if ratio in probe and band_probe:
            band_best = _best(match(frame, template, threshold=THRESHOLD,
                                    scales=scale_scan(1.0)[0]))
            row["band_score"] = round(band_best["score"], 4) if band_best else None
            row["band_scale"] = band_best["scale"] if band_best else None
        rows.append(row)
    accepted = [row["true_ratio"] for row in rows if row["single_accepts_at_0_80"]]
    strong = [row["true_ratio"] for row in rows if row["single_strong_at_0_92"]]
    return {
        "template_label": label,
        "frame_size": list(DRIFT_FRAME_SIZE),
        "template_size": [template.shape[1], template.shape[0]],
        "single_scale": 1.0,
        "acceptance_threshold": THRESHOLD,
        "strong_threshold": STRONG,
        "ratio_rows": rows,
        "single_scale_accepts_ratio_range": (
            [min(accepted), max(accepted)] if accepted else None),
        "single_scale_strong_ratio_range": (
            [min(strong), max(strong)] if strong else None),
    }


PRODUCT_PAIRS = [
    ("same product (1.3021 vs 1.3021)", 1.3021, 1.3021),
    ("125% DPI as 125/96 vs stored 4dp", 1.3020833333333333, 1.3021),
    ("125% DPI rounded to 1.3 vs 1.3021", 1.3, 1.3021),
    ("dpr 1.25 x zoom 1.0 vs 1.3021", 1.25, 1.3021),
    ("150% DPI 1.5625 vs 1.3021", 1.5625, 1.3021),
    ("dpr 1.25 x zoom 1.1 = 1.375 vs 1.3021", 1.375, 1.3021),
]


def product_pairs(drifts: list[dict]) -> dict:
    """Classify realistic product pairings against both measured ranges."""
    rows = []
    for label, current, stored in PRODUCT_PAIRS:
        ratio = current / stored
        verdicts = {}
        for drift in drifts:
            accepted = drift["single_scale_accepts_ratio_range"]
            low, high = (accepted if accepted else (None, None))
            verdicts[drift["template_label"]] = {
                "range": [low, high],
                "inside": bool(low is not None and low <= ratio <= high),
            }
        rows.append({
            "pair": label,
            "ratio": round(ratio, 6),
            "exact_same_product": current == stored,
            "single_scale_verdicts": verdicts,
        })
    return {"rows": rows,
            "ranges": {drift["template_label"]:
                       drift["single_scale_accepts_ratio_range"]
                       for drift in drifts}}


def main() -> None:
    sweep = sweep_cost()
    drifts = [
        drift_curve(_template(32, 120), label="control-block-bar", band_probe=True),
        drift_curve(_glyph_template(), label="glyph-text", band_probe=False),
    ]
    pairs = product_pairs(drifts)
    report = {
        "environment": {
            "python": sys.version.split()[0],
            "cv2": cv2.__version__,
            "numpy": np.__version__,
        },
        "frame_budget": {
            "note": ("find_anchor captures once per call; frames_captured=1 and "
                     "frame_reused=true are asserted in tests/test_anchors.py"),
            "tests": [
                "test_find_anchor_captures_one_frame_for_every_candidate",
                "test_find_anchor_fallback_scores_on_the_same_frame",
                "test_find_anchor_capture_failure_is_attempted_once",
            ],
        },
        "sweep_cost": sweep,
        "drift_curves": drifts,
        "product_pairs": pairs,
    }
    print(json.dumps(report, indent=2, allow_nan=False, default=str))


if __name__ == "__main__":
    main()
