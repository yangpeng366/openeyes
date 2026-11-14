"""Scene probes: many anchors, one frame; and wait verdicts.

Synthetic frames only, no real window. OpenCV-dependent tests skip with a
named reason when the optional extra is absent.
"""
from __future__ import annotations

import importlib.util

import numpy as np
import pytest
from PIL import Image

from openeyes.backends import anchor as anchor_backend

CV2_AVAILABLE = importlib.util.find_spec("cv2") is not None
requires_cv2 = pytest.mark.skipif(
    not CV2_AVAILABLE,
    reason='OpenCV is not installed; pip install "openeyes[anchors]" to run matching tests',
)


def _texture(seed: int = 7, height: int = 34, width: int = 42) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.integers(0, 256, size=(height, width), dtype=np.uint8)


def _paste(image: np.ndarray, template: np.ndarray, x: int, y: int) -> None:
    height, width = template.shape[:2]
    image[y:y + height, x:x + width] = template


def _frame(size=(300, 400)) -> np.ndarray:
    height, width = size
    return np.full((height, width), 20, dtype=np.uint8)


def _save(tmp_path, name: str, template: np.ndarray, rect) -> None:
    # A modern capture records the render product, so window-DPI inference
    # has a group to find. Legacy entries carry only dpi_scale.
    anchor_backend.save_anchor(
        tmp_path, "trae", name, Image.fromarray(template), rect, score=1.0,
        scale_product=1.0,
    )


def _patch_window(monkeypatch, left=100, top=50):
    """Stable window geometry and DPI; capture stays injected per test."""
    monkeypatch.setattr(
        anchor_backend, "_window_rect",
        lambda hwnd: (left, top, left + 400, top + 300),
    )
    monkeypatch.setattr(anchor_backend, "_dpi_scale", lambda hwnd: 1.0)


@requires_cv2
def test_probe_matches_many_anchors_against_one_capture(monkeypatch, tmp_path):
    solo, send = _texture(seed=11), _texture(seed=12)
    image = _frame()
    _paste(image, solo, 10, 10)
    _paste(image, send, 250, 200)
    _save(tmp_path, "solo_badge", solo, [10, 10, 42, 34])
    _save(tmp_path, "send_button", send, [250, 200, 42, 34])
    _patch_window(monkeypatch)
    calls: list[int] = []

    def capture(hwnd):
        calls.append(hwnd)
        return Image.fromarray(image)

    receipt = anchor_backend.probe_anchors(
        4242, tmp_path, ["solo_badge", "send_button"], app="trae", capture=capture,
    )

    assert receipt["ok"] is True
    assert receipt["satisfied"] is True
    assert receipt["matched"] == ["solo_badge", "send_button"]
    assert receipt["missing"] == [] and receipt["ambiguous"] == []
    assert calls == [4242], "a probe must capture exactly once, whatever the anchor count"
    assert receipt["state"]["solo_badge"]["screen_point"] == {"x": 131, "y": 77}
    assert receipt["state"]["send_button"]["screen_point"] == {"x": 371, "y": 267}
    assert receipt["frame"]["size"] == [400, 300]
    assert len(receipt["frame"]["sha256"]) == 16
    assert receipt["frame"]["window_rect"] == [100, 50, 500, 350]
    assert receipt["scale_source"] == "window-dpi"
    assert receipt["state"]["solo_badge"]["used_group"] is True


@requires_cv2
def test_probe_reports_the_missing_name_without_failing_the_frame(monkeypatch, tmp_path):
    solo = _texture(seed=21)
    image = _frame()
    _paste(image, solo, 10, 10)
    _save(tmp_path, "solo_badge", solo, [10, 10, 42, 34])
    _save(tmp_path, "send_button", _texture(seed=22), [250, 200, 42, 34])
    _patch_window(monkeypatch)

    receipt = anchor_backend.probe_anchors(
        4242, tmp_path, ["solo_badge", "send_button"], app="trae",
        capture=lambda hwnd: Image.fromarray(image),
    )

    assert receipt["ok"] is True
    assert receipt["satisfied"] is False
    assert receipt["matched"] == ["solo_badge"]
    assert receipt["missing"] == ["send_button"]
    assert receipt["error_code"] == anchor_backend.ERROR_ANCHOR_NOT_FOUND
    assert "send_button" in receipt["error"]


@requires_cv2
def test_probe_refuses_to_pick_between_two_identical_controls(monkeypatch, tmp_path):
    solo = _texture(seed=31)
    image = _frame()
    _paste(image, solo, 10, 10)
    _paste(image, solo, 200, 150)
    _save(tmp_path, "solo_badge", solo, [10, 10, 42, 34])
    _patch_window(monkeypatch)

    receipt = anchor_backend.probe_anchors(
        4242, tmp_path, ["solo_badge"], app="trae",
        capture=lambda hwnd: Image.fromarray(image),
    )

    assert receipt["ok"] is True
    assert receipt["satisfied"] is False
    assert receipt["ambiguous"] == ["solo_badge"]
    assert receipt["state"]["solo_badge"]["found"] is False
    assert receipt["error_code"] == anchor_backend.ERROR_ANCHOR_AMBIGUOUS


@requires_cv2
def test_probe_marks_missing_material_as_unreadable(monkeypatch, tmp_path):
    solo = _texture(seed=41)
    image = _frame()
    _paste(image, solo, 10, 10)
    _save(tmp_path, "solo_badge", solo, [10, 10, 42, 34])
    entry_file = anchor_backend.list_anchors(tmp_path, "trae")[0]["file"]
    (tmp_path / "trae" / entry_file).unlink()
    _patch_window(monkeypatch)

    receipt = anchor_backend.probe_anchors(
        4242, tmp_path, ["solo_badge"], app="trae",
        capture=lambda hwnd: Image.fromarray(image),
    )

    assert receipt["ok"] is False
    assert receipt["unreadable"] == ["solo_badge"]
    assert receipt["error_code"] == anchor_backend.ERROR_ANCHOR_TEMPLATE_MISSING


@requires_cv2
def test_probe_marks_an_unknown_name_as_unreadable(monkeypatch, tmp_path):
    solo = _texture(seed=51)
    _save(tmp_path, "solo_badge", solo, [10, 10, 42, 34])
    _patch_window(monkeypatch)

    receipt = anchor_backend.probe_anchors(
        4242, tmp_path, ["never_captured"], app="trae",
        capture=lambda hwnd: Image.fromarray(_frame()),
    )

    assert receipt["ok"] is False
    assert receipt["unreadable"] == ["never_captured"]
    assert receipt["state"]["never_captured"]["reason"] == "anchor-not-in-manifest"


@requires_cv2
def test_probe_auto_scale_off_keeps_the_library_sweep(monkeypatch, tmp_path):
    solo = _texture(seed=61)
    image = _frame()
    _paste(image, solo, 10, 10)
    _save(tmp_path, "solo_badge", solo, [10, 10, 42, 34])
    _patch_window(monkeypatch)

    receipt = anchor_backend.probe_anchors(
        4242, tmp_path, ["solo_badge"], app="trae", auto_scale=False,
        capture=lambda hwnd: Image.fromarray(image),
    )

    assert receipt["satisfied"] is True
    assert receipt["scale_source"] == "none"
    assert receipt["scale_product"] is None
    assert receipt["state"]["solo_badge"]["scan_strategy"]["mode"] == "fallback"


def test_probe_refuses_a_minimized_window_before_capturing(monkeypatch, tmp_path):
    class _FakeGui:
        @staticmethod
        def IsWindow(hwnd):
            return True

        @staticmethod
        def IsIconic(hwnd):
            return True

    monkeypatch.setattr(anchor_backend, "_require_windows",
                        lambda: (_FakeGui, None))
    calls: list[int] = []
    monkeypatch.setattr(anchor_backend, "_capture_window",
                        lambda hwnd: calls.append(hwnd))

    receipt = anchor_backend.probe_anchors(
        4242, tmp_path, ["solo_badge"], app="trae",
    )

    assert receipt["error_code"] == anchor_backend.ERROR_WINDOW_MINIMIZED
    assert "minimized" in receipt["error"]
    assert calls == [], "a minimized window must not be captured"


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def _probe_receipt(matched, ambiguous=(), frame="f0"):
    return {
        "ok": True,
        "requested": ["send_button"],
        "matched": list(matched),
        "ambiguous": list(ambiguous),
        "missing": [] if matched or ambiguous else ["send_button"],
        "frame": {"sha256": frame},
    }


def test_wait_returns_as_soon_as_the_anchor_appears():
    clock = _Clock()
    frames = [_probe_receipt([], frame="f0"),
              _probe_receipt([], frame="f0"),
              _probe_receipt(["send_button"], frame="f1")]

    receipt = anchor_backend.wait_anchors(
        4242, "unused", ["send_button"], until="present", timeout=30,
        interval=1, probe=lambda *a, **k: frames.pop(0),
        sleeper=clock.sleep, clock=clock,
    )

    assert receipt["ok"] is True
    assert receipt["satisfied"] is True
    assert receipt["polls"] == 3
    assert receipt["elapsed_s"] == 2.0
    assert receipt["frame_changes"] == 1


def test_wait_times_out_with_a_verdict_not_an_exception():
    clock = _Clock()

    receipt = anchor_backend.wait_anchors(
        4242, "unused", ["send_button"], until="present", timeout=2,
        interval=1, probe=lambda *a, **k: _probe_receipt([]),
        sleeper=clock.sleep, clock=clock,
    )

    assert receipt["ok"] is False
    assert receipt["satisfied"] is False
    assert receipt["error_code"] == anchor_backend.ERROR_ANCHOR_TIMEOUT
    assert receipt["polls"] == 3
    assert receipt["last"]["requested"] == ["send_button"]


def test_wait_absent_counts_an_ambiguous_hit_as_still_visible():
    clock = _Clock()

    receipt = anchor_backend.wait_anchors(
        4242, "unused", ["send_button"], until="absent", timeout=2,
        interval=1, probe=lambda *a, **k: _probe_receipt([], ["send_button"]),
        sleeper=clock.sleep, clock=clock,
    )

    assert receipt["ok"] is False
    assert receipt["error_code"] == anchor_backend.ERROR_ANCHOR_TIMEOUT


def test_wait_rejects_an_unknown_target():
    with pytest.raises(ValueError):
        anchor_backend.wait_anchors(
            4242, "unused", ["send_button"], until="maybe",
            probe=lambda *a, **k: _probe_receipt([]),
            sleeper=lambda s: None, clock=_Clock(),
        )

@requires_cv2
def test_probe_roi_narrows_the_search_and_keeps_frame_coordinates(tmp_path):
    solo = _texture(seed=71)
    image = _frame()
    _paste(image, solo, 10, 10)
    _paste(image, solo, 260, 60)          # same pixels, far from the stored rect
    _save(tmp_path, "solo_badge", solo, [10, 10, 42, 34])

    full = anchor_backend.probe_anchors_in_image(
        Image.fromarray(image), tmp_path, ["solo_badge"], app="trae",
    )
    roi = anchor_backend.probe_anchors_in_image(
        Image.fromarray(image), tmp_path, ["solo_badge"], app="trae", roi_pad=20,
    )

    assert full["ambiguous"] == ["solo_badge"]
    assert roi["matched"] == ["solo_badge"]
    assert roi["state"]["solo_badge"]["center"] == {"x": 31, "y": 27}
    assert roi["anchors"]["solo_badge"]["search_region"]["size"] == [72, 64]
    assert roi["anchors"]["solo_badge"]["image_size"] == [400, 300]


@requires_cv2
def test_probe_roi_reports_a_moved_control_as_missing(tmp_path):
    solo = _texture(seed=72)
    image = _frame()
    _paste(image, solo, 300, 250)         # the control moved far from the capture
    _save(tmp_path, "solo_badge", solo, [10, 10, 42, 34])

    roi = anchor_backend.probe_anchors_in_image(
        Image.fromarray(image), tmp_path, ["solo_badge"], app="trae", roi_pad=10,
    )

    assert roi["missing"] == ["solo_badge"]
    assert roi["ok"] is True
    assert roi["satisfied"] is False
