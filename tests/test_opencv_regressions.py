"""Focused regressions for the OpenCV score-shape paths in the review.

Everything here is synthetic and self-contained:

* ``matchTemplate`` is stubbed with controlled score maps, so ``_bounded``,
  the dilate local-maxima suppression and ``minMaxLoc`` are exercised on
  exact NaN/+Inf/-Inf inputs without depending on image correlations.
* shapes are drawn on in-memory PIL frames, and ``_coverage`` is fed
  hand-computed boxes.

OpenCV is a hard dependency of these paths; when it is absent the whole file
is skipped with a named reason (never a silent pass).  No production file is
changed here.
"""
from __future__ import annotations

import math

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

from openeyes.core import scene
from openeyes.core.anchors import _best_match_score, _bounded, match

cv2 = pytest.importorskip(
    "cv2", reason="OpenCV is required for the NaN/Inf and contour regressions"
)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _images():
    """A non-constant source/template pair that passes the variance gate."""
    rng = np.random.default_rng(0)
    image = rng.integers(0, 256, (40, 40), dtype=np.uint8)
    template = rng.integers(0, 256, (8, 8), dtype=np.uint8)
    return image, template


def _install_score_map(monkeypatch, score_map):
    """Force matchTemplate to return a controlled float32 score map.

    The shape of the map is irrelevant to the assertions: ``match`` and
    ``_best_match_score`` read it through ``_bounded``/dilate/minMaxLoc, and
    the resulting hit coordinates come straight from its indices.
    """
    arr = np.asarray(score_map, dtype=np.float32)
    monkeypatch.setattr(
        cv2, "matchTemplate", lambda source, templ, method: arr.copy()
    )
    return arr


def _shape_frame(width: int = 400, height: int = 240) -> Image.Image:
    return Image.new("RGB", (width, height), (232, 232, 232))


def _kinds(image: Image.Image) -> list[str]:
    return [shape["kind"] for shape in scene._detect_shapes(image)]


def _ui_font(size: int):
    """A real TrueType UI face, or skip: the glyph gate needs real outlines."""
    for name in ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf", "Helvetica.ttc"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    pytest.skip("no TrueType UI font is available for the glyph regression")


# --------------------------------------------------------------------------- #
# 1. anchors.py: _bounded / local-maxima suppression / minMaxLoc
# --------------------------------------------------------------------------- #
def test_bounded_maps_nan_and_both_infinities():
    raw = np.array(
        [[np.nan, np.inf, -np.inf, 0.5, 1.2, -0.9]], dtype=np.float32
    )
    out = _bounded(raw)[0].tolist()

    assert out[0] == 0.0                       # NaN -> neutral, not a match
    assert out[1] == 0.0                       # +Inf -> invalid evidence
    assert out[2] == 0.0                       # -Inf -> invalid evidence
    assert out[3] == pytest.approx(0.5)
    # Finite values are also bounded to the documented NCC range.
    assert out[4] == pytest.approx(1.0, abs=1e-6)
    assert out[5] == pytest.approx(-0.9, abs=1e-6)


def test_bounded_output_is_always_finite():
    raw = np.array(
        [[np.nan, np.inf, -np.inf], [0.8, -np.inf, np.nan]], dtype=np.float32
    )
    assert np.isfinite(_bounded(raw)).all()


def test_best_match_score_ignores_nan_windows(monkeypatch):
    """Zero-variance source windows produce NaN; they must not corrupt the max."""
    image, template = _images()
    score = np.array(
        [[np.nan, 0.4, 0.4], [0.4, 0.95, 0.4], [0.4, 0.4, np.nan]],
        dtype=np.float32,
    )
    _install_score_map(monkeypatch, score)

    best = _best_match_score(image, template, scales=[1.0])

    assert math.isfinite(best)
    assert best == pytest.approx(0.95)


def test_best_match_score_ignores_infinity(monkeypatch):
    """Non-finite cells are invalid and cannot become a false perfect match."""
    image, template = _images()
    score = np.array(
        [[0.3, 0.3, 0.3], [0.3, np.inf, 0.3], [0.3, 0.3, 0.3]],
        dtype=np.float32,
    )
    _install_score_map(monkeypatch, score)

    best = _best_match_score(image, template, scales=[1.0])

    assert math.isfinite(best)
    assert best == pytest.approx(0.3)
    assert -1.0 <= best <= 1.0


def test_best_match_score_all_nan_is_finite_zero(monkeypatch):
    """An entirely degenerate result must not leak minMaxLoc's -FLT_MAX max."""
    image, template = _images()
    _install_score_map(monkeypatch, np.full((3, 3), np.nan, dtype=np.float32))

    best = _best_match_score(image, template, scales=[1.0])

    assert math.isfinite(best)
    assert best == 0.0


def test_match_emits_only_finite_clamped_scores_with_nan_inf_present(monkeypatch):
    """NaN/Inf in the score map must never reach a returned hit as non-finite."""
    image, template = _images()
    score = np.array(
        [
            [np.nan, 0.5, 0.5, 0.5, 0.5, 0.5, -np.inf],
            [0.5, 0.95, 0.5, 0.5, 0.5, 0.5, 0.5],       # genuine isolated peak
            [0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5],
            [0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5],
            [0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5],
            [0.5, 0.5, 0.5, 0.5, 0.5, np.inf, 0.5],       # invalid overflow
            [0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5],
        ],
        dtype=np.float32,
    )
    _install_score_map(monkeypatch, score)

    hits = match(image, template, threshold=0.80, scales=[1.0])

    assert hits
    for hit in hits:
        assert math.isfinite(hit["score"])
        assert -1.0 <= hit["score"] <= 1.0
        assert hit["score"] >= 0.80            # -Inf (-1.0) is never a hit
    assert any(abs(h["score"] - 0.95) < 1e-6 for h in hits)  # real peak survives


def test_local_maxima_suppression_collapses_a_cluster_to_its_peak(monkeypatch):
    """A broad smooth peak has 9 above-threshold pixels but one local maximum."""
    image, template = _images()
    score = np.array(
        [
            [0.5, 0.5, 0.5, 0.5, 0.5],
            [0.5, 0.8, 0.85, 0.8, 0.5],
            [0.5, 0.85, 0.99, 0.85, 0.5],
            [0.5, 0.8, 0.85, 0.8, 0.5],
            [0.5, 0.5, 0.5, 0.5, 0.5],
        ],
        dtype=np.float32,
    )
    _install_score_map(monkeypatch, score)

    hits = match(image, template, threshold=0.80, scales=[1.0], nms_iou=0.3)

    assert len(hits) == 1
    assert (hits[0]["x"], hits[0]["y"]) == (2, 2)
    assert hits[0]["score"] == pytest.approx(0.99)


# --------------------------------------------------------------------------- #
# 2. scene.py: caret detection on synthetic triangles
# --------------------------------------------------------------------------- #
def test_filled_triangle_is_detected_as_caret():
    image = _shape_frame()
    draw = ImageDraw.Draw(image)
    draw.polygon([(50, 130), (80, 80), (110, 130)], fill=(35, 35, 35))

    shapes = scene._detect_shapes(image)

    assert len(shapes) == 1
    assert shapes[0]["kind"] == "caret"
    assert shapes[0]["bbox"]["w"] >= 8 and shapes[0]["bbox"]["h"] >= 8


def test_wide_flat_triangle_is_not_a_caret():
    """aspect > CARET_MAX_ASPECT: rejected by the caret gate, then by solidity."""
    image = _shape_frame()
    draw = ImageDraw.Draw(image)
    draw.polygon([(50, 80), (90, 60), (130, 80)], fill=(35, 35, 35))

    assert "caret" not in _kinds(image)


def test_circle_and_square_are_not_carets():
    image = _shape_frame()
    draw = ImageDraw.Draw(image)
    draw.ellipse([220, 30, 270, 80], fill=(35, 35, 35))
    draw.rectangle([140, 30, 170, 60], fill=(35, 35, 35))

    assert "caret" not in _kinds(image)


def test_low_solidity_blobs_need_caret_like_vertices():
    """Diamond and plus shapes must not be confused with dropdown carets."""
    diamond = _shape_frame()
    ImageDraw.Draw(diamond).polygon(
        [(80, 40), (120, 80), (80, 120), (40, 80)], fill=(35, 35, 35)
    )
    plus = _shape_frame()
    d = ImageDraw.Draw(plus)
    d.rectangle([70, 40, 90, 120], fill=(35, 35, 35))
    d.rectangle([40, 70, 120, 90], fill=(35, 35, 35))

    assert "caret" not in _kinds(diamond)
    assert "caret" not in _kinds(plus)


@pytest.mark.parametrize("text", ["v", "x", "4", "X", "V", "w", "7",
                                  "v1", "x4", "Vol"])
@pytest.mark.parametrize("size", [13, 16, 20, 28])
def test_text_glyphs_are_never_read_as_carets(text, size):
    """``v`` / ``x`` / ``4`` fill 0.40..0.65 of their box just like a caret.

    A filled dropdown triangle has a *solid* interior (convexity ~1.0, hull
    simplifies to three sides); a glyph stroke is concave.  Before that gate
    the fill-ratio + vertex-count test reported ``X`` at 13 px and ``v`` /
    ``4`` / ``X`` at 20 px as carets.
    """
    image = _shape_frame()
    ImageDraw.Draw(image).text((40, 60), text, fill=(20, 20, 20),
                               font=_ui_font(size))

    assert "caret" not in _kinds(image)


def test_a_solid_triangle_is_still_a_caret_next_to_text():
    """The tighter gate must not throw the real dropdown arrows away."""
    image = _shape_frame()
    draw = ImageDraw.Draw(image)
    draw.text((40, 20), "v4", fill=(20, 20, 20), font=_ui_font(20))
    draw.polygon([(200, 40), (224, 40), (212, 54)], fill=(35, 35, 35))

    kinds = _kinds(image)

    assert kinds.count("caret") == 1


def test_a_small_but_solid_triangle_clears_the_caret_gate():
    image = _shape_frame()
    ImageDraw.Draw(image).polygon(
        [(50, 80), (66, 80), (58, 90)], fill=(35, 35, 35)
    )

    assert "caret" in _kinds(image)


# --------------------------------------------------------------------------- #
# 2b. scene.py: _coverage union of overlapping boxes
# --------------------------------------------------------------------------- #
def test_coverage_union_does_not_double_count_overlapping_boxes():
    box = [0, 0, 10, 10]

    # Two identical half-covers: union 0.5, not a summed 1.0.
    assert scene._coverage(box, [[0, 0, 10, 5], [0, 0, 10, 5]]) == 0.5
    # Two disjoint halves cover the whole box.
    assert scene._coverage(box, [[0, 0, 10, 5], [0, 5, 10, 5]]) == 1.0
    # 50% + 50% with a 25% overlap -> 75% union.
    assert scene._coverage(box, [[0, 0, 10, 5], [0, 0, 5, 10]]) == 0.75


def test_coverage_ignores_boxes_outside_the_target():
    box = [10, 10, 10, 10]

    assert scene._coverage(box, []) == 0.0
    assert scene._coverage(box, [[-100, 0, 10, 10]]) == 0.0   # entirely left
    assert scene._coverage(box, [[100, 10, 10, 10]]) == 0.0    # entirely right


def test_coverage_handles_partial_and_translated_boxes():
    # 'others' are absolute; _coverage translates them into the box's space.
    box = [20, 30, 10, 10]

    assert scene._coverage(box, [[25, 30, 5, 5]]) == 0.25      # one quarter


def test_coverage_zero_area_and_single_negative_dim_return_zero():
    assert scene._coverage([0, 0, 0, 10], [[0, 0, 5, 5]]) == 0.0
    assert scene._coverage([0, 0, -10, 10], [[0, 0, 5, 5]]) == 0.0
    assert scene._coverage([0, 0, 10, -10], [[0, 0, 5, 5]]) == 0.0


def test_coverage_both_negative_dims_should_be_zero():
    """Malformed negative dimensions are safely treated as empty boxes."""
    assert scene._coverage([0, 0, -10, -10], [[0, 0, 5, 5]]) == 0.0
