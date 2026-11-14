"""Synthetic tests for template anchors.

No test here touches a real window. Tests that need OpenCV skip with a named
reason when the optional extra is absent; manifest tests always run.
"""
from __future__ import annotations

import importlib.util
import json

import numpy as np
import pytest
from click.testing import CliRunner
from PIL import Image

from openeyes.backends import anchor as anchor_backend
from openeyes.cli.main import cli
from openeyes.core.anchors import assess_frames, classify, decide, match, nms, scale_scan

CV2_AVAILABLE = importlib.util.find_spec("cv2") is not None
requires_cv2 = pytest.mark.skipif(
    not CV2_AVAILABLE,
    reason='OpenCV is not installed; pip install "openeyes[anchors]" to run matching tests',
)


def _cv2():
    import cv2

    return cv2


def _texture(seed: int = 7, height: int = 34, width: int = 42) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.integers(0, 256, size=(height, width), dtype=np.uint8)


def _paste(image: np.ndarray, template: np.ndarray, x: int, y: int) -> None:
    height, width = template.shape[:2]
    image[y:y + height, x:x + width] = template


def test_scale_scan_prior_and_fallback_paths():
    prior, prior_strategy = scale_scan(1.25)

    assert prior_strategy["mode"] == "scale-prior"
    assert prior_strategy["expected_ratio"] == 1.25
    assert prior_strategy["range"] == [1.125, 1.375]
    assert prior_strategy["step"] == 0.02
    assert 1.25 in prior
    assert all(1.125 <= value <= 1.375 for value in prior)

    fallback, fallback_strategy = scale_scan(None)

    assert fallback_strategy["mode"] == "fallback"
    assert fallback_strategy["range"] == [0.8, 1.25]
    assert fallback_strategy["step"] == 0.05
    assert fallback == [0.8, 0.85, 0.9, 0.95, 1.0, 1.05, 1.1, 1.15, 1.2, 1.25]


@requires_cv2
def test_find_anchor_prefers_matching_scale_product_group(monkeypatch, tmp_path):
    template = _texture(seed=51)
    image = np.full((150, 240), 20, dtype=np.uint8)
    _paste(image, template, 70, 45)
    anchor_backend.save_anchor(
        tmp_path, "trae", "solo_badge", Image.fromarray(template), [70, 45, 42, 34],
        score=1.0, scale_product=1.3021,
    )
    anchor_backend.save_anchor(
        tmp_path, "trae", "solo_badge", Image.fromarray(_texture(seed=52)),
        [70, 45, 42, 34], score=1.0, scale_product=1.5625,
    )
    _patch_live_window(monkeypatch, [Image.fromarray(image)])

    report = anchor_backend.find_anchor(
        4242, tmp_path, "solo_badge", app="trae", scale_product=1.3021
    )

    assert report["ok"] is True
    assert report["used_group"] is True
    assert report["fell_back"] is False
    assert report["scan_strategy"]["mode"] == "scale-prior"
    assert report["scan_strategy"]["expected_ratio"] == 1.0
    assert len(anchor_backend.load_store(tmp_path, "trae")["anchors"]) == 2


@requires_cv2
def test_find_anchor_falls_back_when_scale_group_misses(monkeypatch, tmp_path):
    cv2 = _cv2()
    template = _texture(seed=53)
    fallback_template = cv2.resize(template, (50, 41), interpolation=cv2.INTER_LINEAR)
    image = np.full((150, 240), 20, dtype=np.uint8)
    _paste(image, template, 70, 45)
    anchor_backend.save_anchor(
        tmp_path, "trae", "solo_badge", Image.fromarray(_texture(seed=54)),
        [70, 45, 42, 34], score=1.0, scale_product=1.3021,
    )
    anchor_backend.save_anchor(
        tmp_path, "trae", "solo_badge", Image.fromarray(fallback_template),
        [70, 45, 50, 41], score=1.0, scale_product=1.5625,
    )
    _patch_live_window(monkeypatch, [Image.fromarray(image), Image.fromarray(image)])

    report = anchor_backend.find_anchor(
        4242, tmp_path, "solo_badge", app="trae", scale_product=1.3021,
        weak_support=True,
        threshold=0.80,
    )

    assert report["ok"] is True, json.dumps(report, default=str)
    assert report["used_group"] is False
    assert report["fell_back"] is True
    assert report["scan_strategy"]["mode"] == "scale-prior"
    assert report["scan_strategy"]["expected_ratio"] == pytest.approx(0.8333, abs=0.0001)
    assert report["scale"] == pytest.approx(0.83, abs=0.01)


@requires_cv2
def test_find_anchor_in_image_reports_group_and_scale_strategy(tmp_path):
    template = _texture(seed=64)
    image = np.full((150, 240), 20, dtype=np.uint8)
    _paste(image, template, 70, 45)
    anchor_backend.save_anchor(
        tmp_path, "trae", "solo_badge", Image.fromarray(template), [70, 45, 42, 34],
        score=1.0, scale_product=1.3021,
    )
    anchor_backend.save_anchor(
        tmp_path, "trae", "solo_badge", Image.fromarray(_texture(seed=65)),
        [70, 45, 42, 34], score=1.0, scale_product=1.5625,
    )

    report = anchor_backend.find_anchor_in_image(
        Image.fromarray(image), tmp_path, "solo_badge", app="trae",
        scale_product=1.3021,
    )

    assert report["ok"] is True
    assert report["source"] == "image"
    assert report["used_group"] is True
    assert report["fell_back"] is False
    assert report["scan_strategy"]["mode"] == "scale-prior"
    assert report["peaks"] == 1
    assert report["ambiguous_rate"] == 0.0
@requires_cv2
def test_frame_stability_accepts_identical_injected_frames(monkeypatch):
    _install_fake_windows(monkeypatch)
    template = _texture(seed=55)
    frame = np.full((100, 160), 25, dtype=np.uint8)
    _paste(frame, template, 30, 20)
    frames = iter([Image.fromarray(frame.copy()) for _ in range(3)])

    result = anchor_backend.frame_stability(
        4242, n=3, interval=0, template=Image.fromarray(template),
        capture=lambda hwnd: next(frames),
    )

    assert result["stable"] is True
    assert result["frames"] == 3
    assert result["max_abs_diff"] == 0.0
    assert result["min_score"] > 0.99


@requires_cv2
def test_frame_stability_rejects_a_drifting_frame(monkeypatch):
    _install_fake_windows(monkeypatch)
    template = _texture(seed=57)
    frame = np.full((100, 160), 25, dtype=np.uint8)
    _paste(frame, template, 30, 20)
    blank = np.zeros_like(frame)
    frames = iter([Image.fromarray(frame), Image.fromarray(blank), Image.fromarray(blank)])

    result = anchor_backend.frame_stability(
        4242, n=3, interval=0, template=Image.fromarray(template),
        capture=lambda hwnd: next(frames),
    )

    assert result["stable"] is False
    assert result["max_abs_diff"] > 0
    assert "pixel-diff" in result["reasons"]
    assert result["min_score"] < 0.92


@requires_cv2
def test_frame_stability_accepts_complete_initial_frame_set():
    template = _texture(seed=58)
    frame = np.full((100, 160), 25, dtype=np.uint8)
    _paste(frame, template, 30, 20)
    injected = [Image.fromarray(frame.copy()) for _ in range(3)]

    result = anchor_backend.frame_stability(
        4242, n=3, interval=0, template=Image.fromarray(template),
        initial=injected, capture=lambda hwnd: pytest.fail("capture should not run"),
    )

    assert result["stable"] is True
    assert result["frames"] == 3
    assert result["max_abs_diff"] == 0.0


def test_pixel_only_frame_stability_does_not_need_a_template():
    frame = np.full((60, 90), 37, dtype=np.uint8)
    result = assess_frames([frame, frame.copy()], None)

    assert result["stable"] is True
    assert result["pixel_stable"] is True
    assert result["template_stable"] is True
    assert result["min_score"] is None


@requires_cv2
def test_template_mismatch_is_separate_from_pixel_stability():
    frame = np.full((60, 90), 37, dtype=np.uint8)
    result = assess_frames([frame, frame.copy()], _texture(seed=99))

    assert result["pixel_stable"] is True
    assert result["template_stable"] is False
    assert result["stable"] is False
    assert result["reasons"] == ["template-score"]


@requires_cv2
def test_find_anchor_check_frame_without_match_is_anchor_not_found(monkeypatch, tmp_path):
    template = _texture(seed=60)
    image = np.full((120, 200), 20, dtype=np.uint8)
    anchor_backend.save_anchor(
        tmp_path, "trae", "solo_badge", Image.fromarray(template), [50, 40, 42, 34], score=1.0,
    )
    _patch_live_window(monkeypatch, [Image.fromarray(image)])

    report = anchor_backend.find_anchor(
        4242, tmp_path, "solo_badge", app="trae", check_frame=True
    )

    assert report["ok"] is False
    assert report["error_code"] == anchor_backend.ERROR_ANCHOR_NOT_FOUND
    assert report["frame_stability"] is None


@requires_cv2
def test_find_anchor_check_frame_returns_frame_unstable(monkeypatch, tmp_path):
    template = _texture(seed=59)
    image = np.full((120, 200), 20, dtype=np.uint8)
    _paste(image, template, 50, 40)
    anchor_backend.save_anchor(
        tmp_path, "trae", "solo_badge", Image.fromarray(template), [50, 40, 42, 34], score=1.0,
    )
    blank = np.zeros_like(image)
    _patch_live_window(monkeypatch, [
        Image.fromarray(image), Image.fromarray(blank), Image.fromarray(blank)
    ])

    report = anchor_backend.find_anchor(
        4242, tmp_path, "solo_badge", app="trae", check_frame=True
    )

    assert report["ok"] is False
    assert report["reason"] == "frame-unstable"
    assert report["error_code"] == anchor_backend.ERROR_FRAME_UNSTABLE
    assert report["frame_stability"]["stable"] is False


@requires_cv2
def test_find_anchor_check_frame_passes_before_a_stable_match(monkeypatch, tmp_path):
    template = _texture(seed=61)
    image = np.full((120, 200), 20, dtype=np.uint8)
    _paste(image, template, 50, 40)
    anchor_backend.save_anchor(
        tmp_path, "trae", "solo_badge", Image.fromarray(template), [50, 40, 42, 34], score=1.0,
    )
    _patch_live_window(monkeypatch, [Image.fromarray(image) for _ in range(4)])

    report = anchor_backend.find_anchor(
        4242, tmp_path, "solo_badge", app="trae", check_frame=True
    )

    assert report["ok"] is True
    assert report["frame_stability"]["stable"] is True
    assert report["peaks"] == 1


@requires_cv2
def test_ambiguous_rate_uses_a_bounded_probe_window(monkeypatch, tmp_path):
    template = _texture(seed=63)
    image = np.full((160, 280), 20, dtype=np.uint8)
    _paste(image, template, 20, 30)
    _paste(image, template, 200, 100)
    anchor_backend.save_anchor(
        tmp_path, "trae", "solo_badge", Image.fromarray(template), [20, 30, 42, 34], score=1.0,
    )
    _patch_live_window(monkeypatch, [Image.fromarray(image), Image.fromarray(image)])

    first = anchor_backend.find_anchor(4242, tmp_path, "solo_badge", app="trae", record_probe=True)
    second = anchor_backend.find_anchor(4242, tmp_path, "solo_badge", app="trae", record_probe=True)

    assert first["ambiguous_rate"] == 1.0
    assert first["probe_count"] == 1
    assert second["ambiguous_rate"] == 1.0
    assert second["probe_count"] == 2


@requires_cv2
def test_exact_match_reports_image_pixel_coordinates():
    template = _texture()
    image = np.full((160, 220), 35, dtype=np.uint8)
    _paste(image, template, 70, 50)

    hits = match(image, template, threshold=0.90)

    assert hits
    top = hits[0]
    assert (top["x"], top["y"], top["w"], top["h"]) == (70, 50, 42, 34)
    assert top["score"] > 0.99
    assert top["scale"] == 1.0
    assert set(top) == {"x", "y", "w", "h", "score", "scale"}


@requires_cv2
def test_scaled_match_recovers_the_scale_factor():
    cv2 = _cv2()
    template = _texture(seed=11)
    expected_scale = 1.15
    width = int(round(template.shape[1] * expected_scale))
    height = int(round(template.shape[0] * expected_scale))
    scaled = cv2.resize(template, (width, height), interpolation=cv2.INTER_LINEAR)
    image = np.full((180, 240), 20, dtype=np.uint8)
    _paste(image, scaled, 80, 60)

    hits = match(image, template, threshold=0.80)

    assert hits
    top = hits[0]
    assert (top["x"], top["y"], top["w"], top["h"]) == (80, 60, width, height)
    assert top["scale"] == expected_scale
    assert top["score"] > 0.92


@requires_cv2
def test_multiple_instances_are_refused_as_ambiguous():
    template = _texture(seed=23)
    image = np.full((180, 260), 10, dtype=np.uint8)
    _paste(image, template, 15, 25)
    _paste(image, template, 180, 105)

    hits = match(image, template, threshold=0.90)
    decision = decide(hits)

    assert len(hits) >= 2
    assert decision["chosen"] is None
    assert decision["ambiguous"] is True
    assert "multiple" in decision["reason"]



@requires_cv2
def test_constant_template_is_rejected_instead_of_matching_everywhere():
    image = np.full((80, 120), 25, dtype=np.uint8)
    template = np.full((20, 24), 25, dtype=np.uint8)

    with pytest.raises(ValueError, match="variance is too low"):
        match(image, template)


def test_default_match_threshold_covers_the_full_weak_tier():
    import inspect

    assert inspect.signature(match).parameters["threshold"].default == 0.80

def test_weak_scores_need_explicit_support():
    weak = {"x": 1, "y": 2, "w": 3, "h": 4, "score": 0.86, "scale": 1.0}
    strong = {"x": 10, "y": 20, "w": 30, "h": 40, "score": 0.97, "scale": 1.0}

    assert classify(0.80) == "weak"
    assert classify(0.919) == "weak"
    assert classify(0.92) == "strong"
    assert classify(0.79) == "miss"

    rejected = decide([weak])
    assert rejected["chosen"] is None
    assert rejected["strength"] == "weak"
    assert rejected["ambiguous"] is False
    assert "weak_support" in rejected["reason"]

    accepted = decide([weak], weak_support=True)
    assert accepted["chosen"] == weak
    assert accepted["strength"] == "weak"
    assert accepted["ambiguous"] is False

    strong_wins = decide([weak, strong], weak_support=True)
    assert strong_wins["chosen"] == strong
    assert strong_wins["strength"] == "strong"


def test_decide_unique_and_non_unique_branches():
    first = {"x": 0, "y": 0, "w": 10, "h": 10, "score": 0.99, "scale": 1.0}
    second = {"x": 30, "y": 30, "w": 10, "h": 10, "score": 0.94, "scale": 1.0}

    unique = decide([first, second])
    assert unique["chosen"] is None
    assert unique["ambiguous"] is True

    best_effort = decide([first, second], require_unique=False)
    assert best_effort["chosen"] == first
    assert best_effort["ambiguous"] is True
    assert "best of 2" in best_effort["reason"]

    empty = decide([])
    assert empty == {
        "chosen": None,
        "strength": "miss",
        "ambiguous": False,
        "reason": "no match at or above the weak threshold",
    }


def test_weak_competitor_blocks_a_strong_click():
    strong = {"x": 0, "y": 0, "w": 10, "h": 10, "score": 0.95, "scale": 1.0}
    near_weak = {"x": 60, "y": 60, "w": 10, "h": 10, "score": 0.90, "scale": 1.0}

    blocked = decide([strong, near_weak])
    assert blocked["chosen"] is None
    assert blocked["ambiguous"] is True
    assert blocked["strength"] == "strong"
    assert "within 0.06" in blocked["reason"]

    far_weak = {"x": 120, "y": 120, "w": 10, "h": 10, "score": 0.86, "scale": 1.0}
    picked = decide([strong, far_weak])
    assert picked["chosen"] == strong
    assert picked["ambiguous"] is False

    assert decide([strong, near_weak], margin=0.02)["chosen"] == strong

    exact_margin = {"x": 90, "y": 90, "w": 10, "h": 10,
                    "score": 0.89, "scale": 1.0}
    assert decide([strong, exact_margin])["ambiguous"] is True

def test_nms_merges_overlapping_hits_and_keeps_the_best_score():
    hits = [
        {"x": 10, "y": 10, "w": 40, "h": 30, "score": 0.93, "scale": 1.0},
        {"x": 12, "y": 11, "w": 40, "h": 30, "score": 0.98, "scale": 1.05},
        {"x": 110, "y": 80, "w": 40, "h": 30, "score": 0.91, "scale": 1.0},
    ]

    kept = nms(hits, iou_threshold=0.3)

    assert len(kept) == 2
    assert kept[0]["score"] == 0.98
    assert kept[1]["x"] == 110


def test_nms_merges_one_control_seen_at_two_neighbouring_scales():
    hits = [
        {"x": 100, "y": 100, "w": 24, "h": 18, "score": 0.95, "scale": 1.00},
        {"x": 101, "y": 100, "w": 25, "h": 18, "score": 0.94, "scale": 1.02},
    ]

    kept = nms(hits)

    assert len(kept) == 1
    assert kept[0]["score"] == 0.95
    assert kept[0]["scale"] == 1.00


def test_nms_keeps_two_same_scale_neighbours():
    hits = [
        {"x": 100, "y": 100, "w": 24, "h": 18, "score": 0.95, "scale": 1.0},
        {"x": 140, "y": 100, "w": 24, "h": 18, "score": 0.94, "scale": 1.0},
    ]

    assert len(nms(hits)) == 2


def test_nms_keeps_far_apart_instances_even_across_scales():
    hits = [
        {"x": 100, "y": 100, "w": 24, "h": 18, "score": 0.95, "scale": 1.00},
        {"x": 500, "y": 300, "w": 26, "h": 19, "score": 0.93, "scale": 1.08},
    ]

    assert len(nms(hits)) == 2


def test_nms_keeps_a_differently_sized_box_at_the_same_centre():
    hits = [
        {"x": 100, "y": 100, "w": 20, "h": 20, "score": 0.95, "scale": 1.00},
        {"x": 96, "y": 96, "w": 28, "h": 28, "score": 0.70, "scale": 1.10},
    ]

    assert len(nms(hits)) == 2


def test_nms_without_scale_falls_back_to_iou():
    overlapping = [
        {"x": 10, "y": 10, "w": 40, "h": 30, "score": 0.93},
        {"x": 12, "y": 11, "w": 40, "h": 30, "score": 0.98},
    ]
    touching = [
        {"x": 10, "y": 10, "w": 40, "h": 30, "score": 0.93},
        {"x": 60, "y": 10, "w": 40, "h": 30, "score": 0.92},
    ]

    assert len(nms(overlapping)) == 1
    assert len(nms(touching)) == 2


def test_nms_closes_a_cross_scale_chain():
    """0.80 / 1.00 / 1.25 is one control: the ends are 1.56x apart, so a rule
    that only compares with what survived would keep both ends."""
    hits = [
        {"x": 100, "y": 100, "w": 20, "h": 16, "score": 0.95, "scale": 1.00},
        {"x": 99, "y": 100, "w": 26, "h": 21, "score": 0.94, "scale": 1.25},
        {"x": 102, "y": 101, "w": 16, "h": 13, "score": 0.93, "scale": 0.80},
    ]

    kept = nms(hits)

    assert len(kept) == 1
    assert kept[0]["scale"] == 1.00


def test_nms_does_not_merge_padded_neighbours_across_scales():
    """Two 40x40 crops around 16 px icons 18 px apart are two controls.

    A half-box window merged them into one click target; capture padding must
    not turn a neighbour into a scale duplicate.
    """
    hits = [
        {"x": 100, "y": 100, "w": 40, "h": 40, "score": 0.94, "scale": 1.00},
        {"x": 118, "y": 100, "w": 41, "h": 41, "score": 0.93, "scale": 1.04},
    ]

    assert len(nms(hits)) == 2


def test_two_weak_rivals_are_ambiguous_not_merely_weak():
    first = {"x": 10, "y": 10, "w": 10, "h": 10, "score": 0.859, "scale": 1.0}
    second = {"x": 90, "y": 90, "w": 10, "h": 10, "score": 0.855, "scale": 1.0}

    alone = decide([first])
    pair = decide([first, second])

    assert alone["ambiguous"] is False
    assert pair["ambiguous"] is True
    assert pair["chosen"] is None


@requires_cv2
def test_a_missing_template_file_is_a_clear_error(tmp_path):
    image = np.zeros((60, 80), dtype=np.uint8)

    with pytest.raises(FileNotFoundError, match="does not exist"):
        match(image, tmp_path / "missing.png")


def test_manifest_round_trip_and_upsert(tmp_path):
    from openeyes.backends.anchor import (
        AnchorStoreError,
        list_anchors,
        load_store,
        save_anchor,
    )

    store = tmp_path / "anchors"
    # rect is window-relative, so x/y may be outside the crop's own extent.
    image = Image.new("RGB", (40, 24), color=(30, 60, 90))
    first = save_anchor(
        store,
        "trae",
        "solo_badge",
        image,
        [10, 12, 40, 24],
        score=1.0,
        theme="dark",
        dpi_scale=1.25,
        window_class="Chrome_WidgetWin_1",
        exe_name="Trae CN.exe",
        captured_at="2026-11-14T10:00:00+00:00",
        dpi_awareness="per-monitor-v2",
    )

    manifest = load_store(store, "trae")
    assert manifest["app"] == "trae"
    assert manifest["window_class"] == "Chrome_WidgetWin_1"
    assert manifest["exe_name"] == "Trae CN.exe"
    assert manifest["captured_at"] == "2026-11-14T10:00:00+00:00"
    assert manifest["dpi_awareness"] == "per-monitor-v2"
    assert first["rect_window"] == [10, 12, 40, 24]
    assert first["strength"] == "strong"
    assert first["theme"] == "dark"
    assert first["dpi_scale"] == 1.25
    assert len(first["sha256"]) == 64
    assert (store / "trae" / "solo_badge.png").is_file()

    listed = list_anchors(store)
    assert len(listed) == 1
    assert listed[0]["app"] == "trae"
    assert listed[0]["name"] == "solo_badge"

    save_anchor(
        store,
        "trae",
        "solo_badge",
        image,
        [10, 12, 40, 24],
        score=0.95,
        theme="light",
    )
    manifest = load_store(store, "trae")
    assert len(manifest["anchors"]) == 1
    assert manifest["anchors"][0]["score"] == 0.95
    assert manifest["anchors"][0]["theme"] == "light"

    with pytest.raises(AnchorStoreError, match="does not exist"):
        load_store(store, "missing-app")


def test_cli_anchor_list_is_json_and_reports_entries(tmp_path):
    from openeyes.backends.anchor import save_anchor

    store = tmp_path / "anchors"
    save_anchor(
        store,
        "trae",
        "solo_badge",
        Image.new("RGB", (40, 30), color=(1, 2, 3)),
        [0, 0, 20, 10],
        score=1.0,
    )

    result = CliRunner().invoke(
        cli, ["anchor", "list", "--store", str(store), "--json"]
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["count"] == 1
    assert payload["anchors"][0]["name"] == "solo_badge"
    assert payload["anchors"][0]["app"] == "trae"


class _FakeWin32Gui:
    """Minimal win32gui stand-in for the four safety gates."""

    def __init__(self, ancestor: int | None = None, class_name: str = "Chrome_WidgetWin_1"):
        self.ancestor = ancestor
        self.class_name = class_name

    def IsWindow(self, hwnd: int) -> bool:
        return True

    def IsIconic(self, hwnd: int) -> bool:
        return False

    def GetAncestor(self, hwnd: int, flags: int) -> int:
        return hwnd if self.ancestor is None else self.ancestor

    def GetClassName(self, hwnd: int) -> str:
        return self.class_name


def _install_fake_windows(monkeypatch, *, ancestor: int | None = None,
                            class_name: str = "Chrome_WidgetWin_1") -> _FakeWin32Gui:
    fake = _FakeWin32Gui(ancestor=ancestor, class_name=class_name)
    constants = type("FakeWin32Con", (), {"GA_ROOT": 2})
    monkeypatch.setattr(anchor_backend, "_require_windows", lambda: (fake, constants))
    return fake


def _found_report(hwnd: int, point: tuple[int, int] = (400, 300)) -> dict:
    return {
        "ok": True,
        "found": True,
        "hwnd": hwnd,
        "app": "trae",
        "anchor": "solo_badge",
        "bbox": {"x": 10, "y": 20, "w": 40, "h": 20},
        "center": {"x": 30, "y": 30},
        "screen_point": {"x": point[0], "y": point[1]},
        "score": 0.97,
        "strength": "strong",
        "reason": "unique strong anchor",
        "error_code": None,
        "error": None,
        "hits": [],
        "decision": {},
    }


def _click_with_gates(monkeypatch, *, dry_run: bool, foreground: int | None,
                     is_window: bool = True, is_minimized: bool = False,
                     point_hwnd: int = 4242, clicker=None) -> dict:
    hwnd = 4242
    _install_fake_windows(monkeypatch)
    monkeypatch.setattr(
        anchor_backend, "find_anchor", lambda *args, **kwargs: _found_report(hwnd)
    )
    return anchor_backend.click_anchor(
        hwnd,
        "store",
        "solo_badge",
        dry_run=dry_run,
        foreground=foreground,
        is_window=is_window,
        is_minimized=is_minimized,
        window_from_point=lambda x, y: point_hwnd,
        clicker=clicker,
    )


def test_click_anchor_dry_run_reports_all_four_checks(monkeypatch):
    calls = []

    receipt = _click_with_gates(
        monkeypatch,
        dry_run=True,
        foreground=4242,
        clicker=lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    assert receipt["ok"] is True
    assert receipt["dry_run"] is True
    assert receipt["clicked"] is False
    assert receipt["would_click"] is True
    assert receipt["checks"] == {
        "foreground": True,
        "is_window": True,
        "not_minimized": True,
        "point_on_target": True,
    }
    assert calls == []


def test_click_anchor_refuses_when_foreground_differs(monkeypatch):
    calls = []

    receipt = _click_with_gates(
        monkeypatch,
        dry_run=False,
        foreground=9999,
        clicker=lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    assert receipt["ok"] is False
    assert receipt["clicked"] is False
    assert receipt["error_code"] == anchor_backend.ERROR_FOREGROUND_MISMATCH
    assert receipt["checks"]["foreground"] is False
    assert receipt["checks"]["is_window"] is True
    assert calls == []


def test_click_anchor_refuses_minimized_window(monkeypatch):
    receipt = _click_with_gates(
        monkeypatch, dry_run=False, foreground=4242, is_minimized=True
    )

    assert receipt["ok"] is False
    assert receipt["error_code"] == anchor_backend.ERROR_WINDOW_MINIMIZED
    assert receipt["checks"]["not_minimized"] is False


def test_click_anchor_refuses_occluded_point(monkeypatch):
    receipt = _click_with_gates(
        monkeypatch, dry_run=False, foreground=4242, point_hwnd=777
    )

    assert receipt["ok"] is False
    assert receipt["error_code"] == anchor_backend.ERROR_TARGET_OCCLUDED
    assert receipt["checks"]["point_on_target"] is False


def test_click_anchor_refuses_dead_window_handle(monkeypatch):
    receipt = _click_with_gates(
        monkeypatch, dry_run=False, foreground=4242, is_window=False
    )

    assert receipt["ok"] is False
    assert receipt["error_code"] == anchor_backend.ERROR_INVALID_HWND
    assert receipt["checks"]["is_window"] is False


def test_click_anchor_go_clicks_only_after_all_gates(monkeypatch):
    calls = []

    receipt = _click_with_gates(
        monkeypatch,
        dry_run=False,
        foreground=4242,
        clicker=lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    assert receipt["ok"] is True
    assert receipt["clicked"] is True
    assert receipt["effect_verified"] is False
    assert calls == [((400, 300), {"button": "left", "double": False})]

def _patch_live_window(monkeypatch, images, window_rect=(1000, 500, 1200, 620)):
    """Swap only the capture and OS probes; the matching path stays real."""
    _install_fake_windows(monkeypatch)
    iterator = iter(images)
    monkeypatch.setattr(anchor_backend, "_capture_window", lambda hwnd: next(iterator))
    monkeypatch.setattr(anchor_backend, "_window_rect", lambda hwnd: window_rect)
    monkeypatch.setattr(anchor_backend, "_process_exe", lambda hwnd: "Trae CN.exe")
    monkeypatch.setattr(anchor_backend, "_dpi_scale", lambda hwnd: 1.0)


@requires_cv2
def test_find_anchor_maps_center_to_screen_point(monkeypatch, tmp_path):
    template = _texture(seed=41)
    image = np.full((120, 200), 20, dtype=np.uint8)
    _paste(image, template, 50, 40)
    anchor_backend.save_anchor(
        tmp_path, "trae", "solo_badge",
        Image.fromarray(template), [50, 40, 42, 34], score=1.0,
    )
    _patch_live_window(monkeypatch, [Image.fromarray(image)])

    report = anchor_backend.find_anchor(
        4242, tmp_path, "solo_badge", app="trae"
    )

    assert report["ok"] is True
    assert report["bbox"] == {"x": 50, "y": 40, "w": 42, "h": 34}
    assert report["center"] == {"x": 71, "y": 57}
    assert report["screen_point"] == {"x": 1071, "y": 557}
    assert report["strength"] == "strong"
    assert report["peaks"] == 1
    assert report["ambiguous_rate"] == 0.0
    assert report["used_group"] is False
    assert report["fell_back"] is False
    assert report["scan_strategy"]["mode"] == "fallback"


@requires_cv2
def test_find_anchor_refuses_two_live_instances(monkeypatch, tmp_path):
    template = _texture(seed=43)
    image = np.full((160, 280), 20, dtype=np.uint8)
    _paste(image, template, 20, 30)
    _paste(image, template, 200, 100)
    anchor_backend.save_anchor(
        tmp_path, "trae", "solo_badge",
        Image.fromarray(template), [20, 30, 42, 34], score=1.0,
    )
    _patch_live_window(monkeypatch, [Image.fromarray(image)])

    report = anchor_backend.find_anchor(
        4242, tmp_path, "solo_badge", app="trae"
    )

    assert report["ok"] is False
    assert report["error_code"] == anchor_backend.ERROR_ANCHOR_AMBIGUOUS
    assert report["decision"]["ambiguous"] is True
    assert len(report["hits"]) >= 2
    assert report["peaks"] == len(report["hits"])
    assert report["peaks"] > 1
    assert report["ambiguous_rate"] == 1.0
    assert report["probe_count"] == 1


@requires_cv2
def test_capture_anchor_saves_only_after_second_capture_verifies(monkeypatch, tmp_path):
    template = _texture(seed=45, height=20, width=24)
    first = np.full((100, 160), 30, dtype=np.uint8)
    _paste(first, template, 40, 30)
    _patch_live_window(monkeypatch, [
        Image.fromarray(first), Image.fromarray(first), Image.fromarray(first)
    ])

    report = anchor_backend.capture_anchor(
        4242, "solo_badge", [40, 30, 24, 20], tmp_path, "trae", theme="dark",
        scale_product=1.5625, device_scale_factor=1.2, zoom_level=1.0,
        color_theme="dark", icon_theme="monochrome",
        layout_signature="activitybar-left|sidebar-open|panel-closed|maximized",
    )

    assert report["ok"] is True
    assert report["score"] >= anchor_backend.VERIFY_SCORE
    manifest = anchor_backend.load_store(tmp_path, "trae")
    assert manifest["anchors"][0]["rect_window"] == [40, 30, 24, 20]
    assert manifest["anchors"][0]["theme"] == "dark"
    assert manifest["anchors"][0]["scale_product"] == 1.5625
    assert manifest["anchors"][0]["color_theme"] == "dark"
    assert manifest["anchors"][0]["icon_theme"] == "monochrome"
    assert manifest["anchors"][0]["layout_signature"].startswith("activitybar-left")
    assert report["frame_stability"]["stable"] is True
    assert manifest["exe_name"] == "Trae CN.exe"


@requires_cv2
def test_capture_anchor_ignores_animation_outside_the_crop(monkeypatch, tmp_path):
    template = _texture(seed=49, height=20, width=24)
    first = np.full((100, 160), 30, dtype=np.uint8)
    second = first.copy()
    third = first.copy()
    _paste(first, template, 40, 30)
    _paste(second, template, 40, 30)
    _paste(third, template, 40, 30)
    second[5:10, 5:10] = 240
    third[5:10, 5:10] = 120
    _patch_live_window(monkeypatch, [
        Image.fromarray(first), Image.fromarray(second), Image.fromarray(third)
    ])

    report = anchor_backend.capture_anchor(
        4242, "solo_badge", [40, 30, 24, 20], tmp_path, "trae"
    )

    assert report["ok"] is True
    assert report["frame_stability"]["pixel_stable"] is True
    assert report["frame_stability"]["template_stable"] is True


@requires_cv2
def test_capture_anchor_does_not_save_unverified_crop(monkeypatch, tmp_path):
    template = _texture(seed=47, height=20, width=24)
    first = np.full((100, 160), 30, dtype=np.uint8)
    _paste(first, template, 40, 30)
    second = np.zeros((100, 160), dtype=np.uint8)
    _patch_live_window(monkeypatch, [
        Image.fromarray(first), Image.fromarray(second), Image.fromarray(second)
    ])

    with pytest.raises(anchor_backend.AnchorVerificationError, match="did not verify"):
        anchor_backend.capture_anchor(
            4242, "solo_badge", [40, 30, 24, 20], tmp_path, "trae"
        )

    assert not (tmp_path / "trae" / "manifest.json").exists()
    assert not (tmp_path / "trae" / "solo_badge.png").exists()


@requires_cv2
def test_overlay_draws_hits_onto_the_live_capture(monkeypatch, tmp_path):
    image = np.full((90, 140), 25, dtype=np.uint8)
    monkeypatch.setattr(
        anchor_backend, "_capture_window", lambda hwnd: Image.fromarray(image)
    )
    hits = [
        {"x": 10, "y": 12, "w": 30, "h": 20, "score": 0.95, "scale": 1.0},
        {"x": 70, "y": 50, "w": 30, "h": 20, "score": 0.83, "scale": 1.05},
    ]
    out = tmp_path / "debug" / "overlay.png"

    path = anchor_backend.overlay(4242, hits, out)

    assert path == str(out)
    with Image.open(out) as opened:
        assert opened.size == (140, 90)
        assert opened.convert("RGB").getpixel((10, 12)) != (25, 25, 25)


def test_missing_opencv_points_at_the_optional_extra(monkeypatch):
    import sys

    from openeyes.core.anchors import AnchorUnavailable

    monkeypatch.setitem(sys.modules, "cv2", None)
    with pytest.raises(AnchorUnavailable, match=r'openeyes\[anchors\]'):
        match(np.zeros((10, 10), dtype=np.uint8), np.zeros((4, 4), dtype=np.uint8))


# ---------------------------------------------------------------------------
# Regression guards: the acceptance floor is enforced on the winner only,
# offline receipts stay honest about frame stability, and a legacy dpi_scale
# is still a usable scale group.
# ---------------------------------------------------------------------------

def _peak(score, x, y, w=42, h=34, scale=1.0):
    """One synthetic match hit with an exact, controlled score."""
    return {"x": x, "y": y, "w": w, "h": h, "score": score, "scale": scale}


def _install_peak_match(monkeypatch, peaks):
    """Serve controlled same-size peaks, honouring the match threshold.

    The real ``match`` drops hits below the threshold it is handed; this
    stand-in keeps the scores exact while reproducing that one behaviour, so
    the test is deterministic and does not depend on OpenCV correlations.
    """
    import openeyes.core.anchors as core_anchors

    def fake_match(image, template, threshold=0.80, scales=None, nms_iou=0.3):
        return [dict(hit) for hit in peaks
                if float(hit["score"]) >= float(threshold)]

    monkeypatch.setattr(core_anchors, "match", fake_match)


def _save_one_anchor(tmp_path, seed):
    anchor_backend.save_anchor(
        tmp_path, "trae", "solo_badge",
        Image.fromarray(_texture(seed=seed)), [20, 20, 42, 34], score=1.0,
    )


def _run_find(monkeypatch, tmp_path, *, live, **kwargs):
    if live:
        _patch_live_window(monkeypatch, [Image.new("RGB", (160, 200)) for _ in range(2)])
        return anchor_backend.find_anchor(
            4242, tmp_path, "solo_badge", app="trae", **kwargs)
    return anchor_backend.find_anchor_in_image(
        Image.new("RGB", (160, 200)), tmp_path, "solo_badge", app="trae", **kwargs)


@pytest.mark.parametrize("live", [False, True])
def test_threshold_does_not_hide_a_weak_rival(monkeypatch, tmp_path, live):
    """0.95 next to 0.90 is one ambiguous scene, never a unique strong hit."""
    _save_one_anchor(tmp_path, seed=80)
    _install_peak_match(monkeypatch, [_peak(0.95, 20, 20), _peak(0.90, 120, 90)])

    report = _run_find(monkeypatch, tmp_path, live=live, threshold=0.92)

    assert report["ok"] is False
    assert report["found"] is False
    assert report["error_code"] == anchor_backend.ERROR_ANCHOR_AMBIGUOUS
    assert report["decision"]["ambiguous"] is True
    assert len(report["hits"]) == 2
    assert report["threshold"] == 0.92
    assert report["search_floor"] == 0.80


@pytest.mark.parametrize("live", [False, True])
def test_below_acceptance_floor_is_not_found(monkeypatch, tmp_path, live):
    """A 0.85 winner under a 0.92 floor stays not-found; weak_support
    must not lift it past the acceptance floor."""
    _save_one_anchor(tmp_path, seed=81)
    _install_peak_match(monkeypatch, [_peak(0.85, 60, 50)])

    report = _run_find(
        monkeypatch, tmp_path, live=live, threshold=0.92, weak_support=True)

    assert report["error_code"] == anchor_backend.ERROR_ANCHOR_NOT_FOUND
    assert report["ok"] is False
    assert "acceptance threshold" in report["reason"]
    assert len(report["hits"]) == 1
    assert report["peaks"] == 1
    assert report["score"] == 0.85
    # Refused means no click target: the receipt must not hand back a chosen box.
    assert report["decision"]["chosen"] is None
    assert report["decision"]["candidate"]["score"] == 0.85


@pytest.mark.parametrize("live", [False, True])
def test_peaks_ignore_candidates_below_the_weak_floor(monkeypatch, tmp_path, live):
    """``peaks`` counts what ``decide`` ranks, not sub-0.80 pixels that a low
    ``--threshold`` happened to let through."""
    _save_one_anchor(tmp_path, seed=82)
    _install_peak_match(monkeypatch, [_peak(0.95, 20, 20), _peak(0.60, 100, 80)])

    report = _run_find(monkeypatch, tmp_path, live=live, threshold=0.50)

    assert report["ok"] is True, report.get("reason")
    assert report["peaks"] == 1
    assert len(report["hits"]) == 2
    assert report["decision"]["ambiguous"] is False


def test_group_weak_hit_below_acceptance_still_falls_back(monkeypatch, tmp_path):
    """A 0.85 group hit under a 0.92 floor must not veto a 0.97 sibling.

    Before the search floor was widened this could not happen (0.85 was
    filtered before ``hits`` existed); now the fallback decision has to look
    at the acceptance floor instead of "any hit at all".
    """
    import openeyes.core.anchors as core_anchors

    texture = _texture(seed=83)
    anchor_backend.save_anchor(
        tmp_path, "trae", "solo_badge", Image.fromarray(texture), [20, 20, 42, 34],
        score=1.0, dpi_scale=1.5, scale_product=1.5,
    )
    anchor_backend.save_anchor(
        tmp_path, "trae", "solo_badge", Image.fromarray(_texture(seed=84)),
        [20, 20, 42, 34], score=1.0, dpi_scale=1.25, scale_product=1.25,
    )

    def fake_match(image, template, threshold=0.80, scales=None, nms_iou=0.3):
        group_entry = "@1.5000" in str(template)
        peaks = [_peak(0.85, 20, 20)] if group_entry else [_peak(0.97, 70, 50)]
        return [dict(hit) for hit in peaks
                if float(hit["score"]) >= float(threshold)]

    monkeypatch.setattr(core_anchors, "match", fake_match)

    report = anchor_backend.find_anchor_in_image(
        Image.new("RGB", (160, 200)), tmp_path, "solo_badge", app="trae",
        threshold=0.92, scale_product=1.5,
    )

    assert report["ok"] is True, report.get("reason")
    assert report["score"] == 0.97
    assert report["fell_back"] is True
    assert report["used_group"] is False


def test_save_anchor_replaces_a_legacy_dpi_scale_entry(tmp_path):
    """Re-capturing at the same scale must replace the legacy row, not clone it."""
    app_dir = tmp_path / "trae"
    app_dir.mkdir()
    legacy = {
        "app": "trae",
        "anchors": [{
            "name": "solo_badge",
            "file": "solo_badge.png",
            "dpi_scale": 1.3021,
        }],
    }
    (app_dir / "manifest.json").write_text(json.dumps(legacy), encoding="utf-8")

    anchor_backend.save_anchor(
        tmp_path, "trae", "solo_badge", Image.fromarray(_texture(seed=85)),
        [20, 20, 42, 34], score=1.0, dpi_scale=1.3021, scale_product=1.3021,
    )

    reread = json.loads((app_dir / "manifest.json").read_text(encoding="utf-8"))
    names = [entry["name"] for entry in reread["anchors"]]
    assert names.count("solo_badge") == 1
    assert reread["anchors"][0]["scale_product"] == 1.3021


def test_find_merges_a_duplicated_cross_scale_peak(monkeypatch, tmp_path):
    """One control found at 1.00 and 1.02 is one hit, not an ambiguous scene."""
    import openeyes.core.anchors as core_anchors

    _save_one_anchor(tmp_path, seed=86)

    def fake_match(image, template, threshold=0.80, scales=None, nms_iou=0.3):
        peaks = [_peak(0.95, 40, 40, w=42, h=34, scale=1.00),
                 _peak(0.95, 41, 40, w=43, h=35, scale=1.02)]
        return nms([hit for hit in peaks
                    if float(hit["score"]) >= float(threshold)],
                   iou_threshold=nms_iou)

    monkeypatch.setattr(core_anchors, "match", fake_match)

    report = anchor_backend.find_anchor_in_image(
        Image.new("RGB", (160, 200)), tmp_path, "solo_badge", app="trae")

    assert report["ok"] is True, report.get("reason")
    assert report["peaks"] == 1
    assert report["decision"]["ambiguous"] is False


def test_two_real_instances_still_read_as_ambiguous(monkeypatch, tmp_path):
    _save_one_anchor(tmp_path, seed=87)
    _install_peak_match(monkeypatch, [_peak(0.95, 20, 20), _peak(0.95, 100, 60)])

    report = _run_find(monkeypatch, tmp_path, live=False)

    assert report["error_code"] == anchor_backend.ERROR_ANCHOR_AMBIGUOUS
    assert report["peaks"] == 2


def test_require_unique_false_still_returns_the_best_of_a_contested_scene(monkeypatch, tmp_path):
    """Best-effort callers keep their hit; only uniqueness-required callers refuse.
    """
    _save_one_anchor(tmp_path, seed=88)
    _install_peak_match(monkeypatch, [_peak(0.95, 20, 20), _peak(0.93, 100, 60)])

    report = _run_find(monkeypatch, tmp_path, live=False, require_unique=False)

    assert report["ok"] is True, report.get("reason")
    assert report["decision"]["ambiguous"] is True
    assert report["score"] == 0.95


def test_entry_scale_product_prefers_modern_field_and_stays_none():
    helper = anchor_backend._entry_scale_product
    assert helper({"scale_product": 1.5, "dpi_scale": 1.25}) == 1.5
    assert helper({"dpi_scale": 1.3021}) == 1.3021
    assert helper({}) is None
    assert helper({"scale_product": None}) is None


@requires_cv2
def test_legacy_dpi_scale_is_used_as_the_scale_group(tmp_path):
    app_dir = tmp_path / "trae"
    app_dir.mkdir()
    template = _texture(seed=90)
    Image.fromarray(template).save(app_dir / "solo_badge.png")
    manifest = {
        "app": "trae",
        "anchors": [{
            "name": "solo_badge",
            "file": "solo_badge.png",
            "dpi_scale": 1.3021,
        }],
    }
    (app_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    image = np.full((150, 240), 20, dtype=np.uint8)
    _paste(image, template, 70, 45)

    report = anchor_backend.find_anchor_in_image(
        Image.fromarray(image), tmp_path, "solo_badge", app="trae",
        scale_product=1.3021,
    )

    assert report["ok"] is True, json.dumps(report, default=str)
    assert report["used_group"] is True
    assert report["fell_back"] is False
    assert report["scan_strategy"]["mode"] == "scale-prior"
    assert report["scan_strategy"]["expected_ratio"] == 1.0
    assert len(report["candidates"]) == 1
    reread = json.loads((app_dir / "manifest.json").read_text(encoding="utf-8"))
    assert "scale_product" not in reread["anchors"][0]


def _offline_cli_fixture(tmp_path):
    store = tmp_path / "anchors"
    template = _texture(seed=70)
    anchor_backend.save_anchor(
        store, "trae", "solo_badge", Image.fromarray(template),
        [20, 20, 42, 34], score=1.0,
    )
    image = np.full((120, 200), 20, dtype=np.uint8)
    _paste(image, template, 20, 20)
    image_path = tmp_path / "shot.png"
    Image.fromarray(image).save(image_path)
    return store, image_path


def test_cli_anchor_find_image_check_frame_is_a_usage_error(tmp_path):
    image_path = tmp_path / "shot.png"
    Image.new("RGB", (8, 8)).save(image_path)

    result = CliRunner().invoke(
        cli, ["anchor", "find", "--image", str(image_path),
              "--name", "solo_badge", "--check-frame"])

    assert result.exit_code == 2, result.output
    assert "live window" in result.output


@requires_cv2
def test_cli_anchor_find_image_json_receipt_marks_frame_stability(tmp_path):
    store, image_path = _offline_cli_fixture(tmp_path)

    result = CliRunner().invoke(
        cli, ["anchor", "find", "--image", str(image_path),
              "--name", "solo_badge", "--app", "trae",
              "--store", str(store), "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["found"] is True
    assert payload["frame_stability"] == "not-applicable-offline"


@requires_cv2
def test_cli_anchor_find_image_text_receipt_marks_frame_stability(tmp_path):
    store, image_path = _offline_cli_fixture(tmp_path)

    result = CliRunner().invoke(
        cli, ["anchor", "find", "--image", str(image_path),
              "--name", "solo_badge", "--app", "trae", "--store", str(store)])

    assert result.exit_code == 0, result.output
    assert "frame_stability" in result.output
    assert "not-applicable-offline" in result.output


@requires_cv2
def test_cli_anchor_find_image_failure_json_marks_frame_stability(tmp_path):
    store = tmp_path / "anchors"
    anchor_backend.save_anchor(
        store, "trae", "solo_badge",
        Image.fromarray(_texture(seed=71)), [20, 20, 42, 34], score=1.0,
    )
    image_path = tmp_path / "blank.png"
    Image.fromarray(np.full((120, 200), 20, dtype=np.uint8)).save(image_path)

    result = CliRunner().invoke(
        cli, ["anchor", "find", "--image", str(image_path),
              "--name", "solo_badge", "--app", "trae",
              "--store", str(store), "--json"])

    assert result.exit_code == 3, result.output
    payload = json.loads(result.stdout)
    assert payload["frame_stability"] == "not-applicable-offline"
