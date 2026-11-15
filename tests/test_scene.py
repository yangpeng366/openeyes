"""Scene building: what an agent gets to read about a window.

Synthetic frames and fake engines only - no real window, no OCR package.
"""
from __future__ import annotations

import sys

import numpy as np
import pytest
from PIL import Image, ImageDraw

from openeyes.core import scene as scene_api
from openeyes.core.schema import BBox, Center, Element


def _element(name: str, control_type: str = "Button", x: int = 10, y: int = 10,
             w: int = 80, h: int = 30) -> Element:
    return Element(
        backend="uia",
        control_type=control_type,
        name=name,
        bbox=BBox(x, y, w, h),
        center=Center(x + w // 2, y + h // 2),
        interactive=True,
    )


class _FakeEngine:
    """RapidOCR-shaped callable with a fixed payload."""

    def __init__(self, payload):
        self.payload = payload

    def __call__(self, image):
        return self.payload


def _poly(x0: int, y0: int, x1: int, y1: int) -> list[list[int]]:
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def _frame(width: int = 400, height: int = 200) -> Image.Image:
    return Image.new("RGB", (width, height), (18, 18, 18))


def _info(hwnd: int) -> dict:
    return {"title": "Test", "class_name": "Fake", "rect": [0, 0, 400, 200],
            "size": [400, 200], "dpi_scale": 1.0}


def _info_at(rect: list[int], size: list[int] | None = None,
             dpi: float = 1.0):
    """A window at an arbitrary screen origin."""
    box = list(rect)
    return lambda hwnd: {
        "title": "Test", "class_name": "Fake", "rect": box,
        "size": list(size) if size else [box[2] - box[0], box[3] - box[1]],
        "dpi_scale": dpi,
    }


def _describe(tmp_path, *, engine=None, detect=None, capture=None, **kwargs):
    def default_capture(hwnd):
        return _frame()

    return scene_api.describe_window(
        4242,
        capture=capture or default_capture,
        detect=detect if detect is not None else (lambda hwnd: []),
        engine=engine,
        info=_info,
        **kwargs,
    )


def test_fragments_of_one_line_become_one_element():
    engine = _FakeEngine([
        [_poly(20, 10, 40, 30), "新", 0.9],
        [_poly(42, 11, 70, 31), "任务", 0.95],
    ])

    scene = _describe(None, engine=engine)

    assert scene["ok"] is True
    assert scene["source"] == "ocr"
    assert scene["text_lines"] == ["新任务"]
    assert scene["elements"][0]["text"] == "新任务"
    assert scene["elements"][0]["kind"] == "label"


def test_latin_fragments_keep_a_space_and_cjk_fragments_do_not():
    engine = _FakeEngine([
        [_poly(20, 10, 60, 30), "Send", 0.9],
        [_poly(62, 10, 90, 30), "Message", 0.9],
        [_poly(20, 40, 40, 60), "发送", 0.9],
        [_poly(42, 40, 70, 60), "消息", 0.9],
    ])

    scene = _describe(None, engine=engine)

    assert "Send Message" in scene["text_lines"]
    assert "发送消息" in scene["text_lines"]


def test_ocr_text_on_a_uia_element_is_reported_once():
    engine = _FakeEngine([[_poly(10, 10, 90, 40), "新任务", 0.99]])
    detect = lambda hwnd: [_element("新任务")]  # noqa: E731

    scene = _describe(None, engine=engine, detect=detect)

    texts = [element["text"] for element in scene["elements"]]
    assert texts.count("新任务") == 1
    assert scene["source"] == "mixed"


def test_uia_elements_carry_their_names_and_kinds():
    detect = lambda hwnd: [  # noqa: E731
        _element("确定", "Button"),
        _element("备注", "Edit", x=120),
    ]

    scene = _describe(None, detect=detect)

    kinds = {element["text"]: element["kind"] for element in scene["elements"]}
    assert kinds["确定"] == "button"
    assert kinds["备注"] == "input"
    assert scene["source"] == "uia"
    assert scene["counts"]["button"] == 1


def test_chromium_shell_names_are_dropped():
    """View shells are structure, not content; a reader gains nothing."""
    detect = lambda hwnd: [  # noqa: E731
        _element("ContentsView", "Text"),
        _element("SideEdgeView", "Pane"),
        _element("确定", "Button"),
    ]

    scene = _describe(None, detect=detect)

    texts = [element["text"] for element in scene["elements"]]
    assert texts == ["确定"]


def test_an_unnamed_element_is_reported_by_its_type():
    detect = lambda hwnd: [_element("", "Edit", x=40)]  # noqa: E731

    scene = _describe(None, detect=detect)

    assert scene["elements"][0]["text"] == "Edit"
    assert scene["elements"][0]["kind"] == "input"


def test_a_container_element_does_not_swallow_the_text_inside_it():
    """A whole-window pane must not dedup every OCR line away."""
    detect = lambda hwnd: [_element("Pane", "Pane", x=0, y=0, w=400, h=200)]  # noqa: E731
    engine = _FakeEngine([[_poly(20, 10, 60, 30), "一行字", 0.9]])

    scene = _describe(None, engine=engine, detect=detect)

    assert "一行字" in scene["text_lines"]


    detect = lambda hwnd: [  # noqa: E731
        _element("确定", "Button"),
        _element("备注", "Edit", x=120),
    ]

    scene = _describe(None, detect=detect)

    kinds = {element["text"]: element["kind"] for element in scene["elements"]}
    assert kinds["确定"] == "button"
    assert kinds["备注"] == "input"
    assert scene["source"] == "uia"
    assert scene["counts"]["button"] == 1


def test_max_items_truncates_and_says_so():
    engine = _FakeEngine([
        [_poly(20, 10 * index, 40, 20 + 10 * index), f"行{index}", 0.9]
        for index in range(10)
    ])

    scene = _describe(None, engine=engine, max_items=3)

    assert len(scene["elements"]) == 3
    assert any("truncated" in note for note in scene["notes"])


def test_a_missing_ocr_engine_leaves_the_uia_scene_alone():
    class _Broken:
        def __call__(self, image):
            raise RuntimeError("no engine")

    detect = lambda hwnd: [_element("确定")]  # noqa: E731

    scene = _describe(None, engine=_Broken(), detect=detect)

    assert scene["ok"] is True
    assert scene["elements"][0]["text"] == "确定"
    assert any("ocr-unavailable" in note for note in scene["notes"])


def test_nothing_readable_is_a_failure_with_a_code():
    class _Broken:
        def __call__(self, image):
            raise RuntimeError("no engine")

    scene = _describe(None, engine=_Broken())

    assert scene["ok"] is False
    assert scene["error_code"] == "OCR_UNAVAILABLE"
    assert scene["elements"] == []


def test_filled_boxes_read_as_shapes_by_their_proportions():
    image = Image.new("RGB", (400, 200), (18, 18, 18))
    draw = ImageDraw.Draw(image)
    draw.rectangle([40, 40, 90, 80], fill=(230, 230, 230))   # 51x41 panel
    draw.rectangle([200, 40, 215, 55], fill=(230, 230, 230))  # 16x16 check box

    scene = _describe(None, capture=lambda hwnd: image)

    kinds = {shape["kind"] for shape in scene["shapes"]}
    assert "panel" in kinds
    assert "checkbox" in kinds
    assert any(48 <= shape["bbox"]["w"] <= 54 for shape in scene["shapes"])


def test_the_scene_never_acts():
    calls: list[str] = []

    def capture(hwnd):
        calls.append("capture")
        return _frame()

    scene = _describe(None, capture=capture)

    assert calls == ["capture"]
    assert "click" not in scene


@pytest.mark.skipif(not hasattr(np, "zeros"), reason="numpy missing")
def test_region_restricts_the_frame_and_keeps_window_coordinates():
    image = Image.new("RGB", (400, 200), (18, 18, 18))
    engine = _FakeEngine([[_poly(20, 10, 60, 30), "边角", 0.9]])

    scene = scene_api.describe_window(
        4242,
        capture=lambda hwnd: image,
        detect=lambda hwnd: [],
        engine=engine,
        info=_info,
        region=(100, 50, 300, 150),
    )

    assert scene["ok"] is True
    element = scene["elements"][0]
    assert element["bbox"]["x"] == 120
    assert element["bbox"]["y"] == 60


def test_region_keeps_shape_coordinates_relative_to_the_window(monkeypatch):
    image = _frame()
    monkeypatch.setattr(
        scene_api, "_detect_shapes",
        lambda crop, **_kwargs: [{"kind": "icon", "bbox": {"x": 20, "y": 10, "w": 12, "h": 14}}],
    )
    scene = scene_api.describe_window(
        4242, source="auto", include_ocr=False, include_shapes=True,
        capture=lambda hwnd: image, detect=lambda hwnd: [], info=_info,
        region=(100, 50, 300, 150),
    )

    assert scene["shapes"][0]["bbox"] == {"x": 120, "y": 60, "w": 12, "h": 14}
    assert scene["elements"][0]["bbox"] == {"x": 120, "y": 60, "w": 12, "h": 14}


def test_checkbox_survives_a_200_percent_display():
    """A physical-pixel checkbox keeps its semantic kind at 200% DPI."""
    pytest.importorskip("cv2")

    def scene_at(dpi: float) -> dict:
        side = 32
        image = _frame(800, 600)
        ImageDraw.Draw(image).rectangle(
            [200, 100, 200 + side - 1, 100 + side - 1],
            fill=(230, 230, 230),
        )
        return scene_api.describe_window(
            4242, source="auto", include_ocr=False, include_shapes=True,
            capture=lambda hwnd: image, detect=lambda hwnd: [],
            info=_info_at([0, 0, 800, 600], size=[800, 600], dpi=dpi),
        )

    base = scene_at(1.0)
    scaled = scene_at(2.0)

    assert scene_api.shape_scale({"dpi_scale": 1.0}, (1.0, 1.0)) == 1.0
    assert scene_api.shape_scale({"dpi_scale": 2.0}, (1.0, 1.0)) == 2.0
    assert "checkbox" not in {shape["kind"] for shape in base["shapes"]}
    assert "checkbox" in {shape["kind"] for shape in scaled["shapes"]}
    assert "checkbox" in {element["kind"] for element in scaled["elements"]}
    assert any(note.startswith("shape-scale:") for note in scaled["notes"])
    assert not any(note.startswith("shape-scale:") for note in base["notes"])


def test_shape_thresholds_scale_area_and_side_limits():
    design = scene_api._shape_thresholds(1.0)
    doubled = scene_api._shape_thresholds(2.0)

    assert design["checkbox_max_area"] == scene_api.CHECKBOX_MAX_AREA
    assert doubled["checkbox_max_area"] == 4 * scene_api.CHECKBOX_MAX_AREA
    assert doubled["min_area"] == 4 * scene_api.SHAPE_MIN_AREA
    assert doubled["min_side"] == 2 * scene_api.SHAPE_MIN_SIDE
    assert doubled["bar_max_height"] == 2 * 16
    assert scene_api._shape_kind(
        80, 32, 2560, bar_max_height=doubled["bar_max_height"]
    ) == "bar"
    assert scene_api._shape_kind(80, 32, 2560) == "panel"
    assert scene_api._shape_kind(
        32, 32, 1024, checkbox_max_area=doubled["checkbox_max_area"]
    ) == "checkbox"
    assert scene_api._shape_kind(
        32, 32, 1024, checkbox_max_area=design["checkbox_max_area"]
    ) == "panel"
    assert scene_api._shape_thresholds(0.0) == design
    assert scene_api.shape_scale({}, (1.0, 1.0)) == 1.0
    assert scene_api.shape_scale({"dpi_scale": None}) == 1.0


def test_uia_elements_keep_the_backend_window_space_and_the_origin_is_reported():
    """The uia backend hands back window-relative pixels; the scene keeps them.

    The window origin is reported once (``window.rect``) so a reader can map
    a centre to the screen.  Nothing shifts the box here: shifting it again is
    how the CLI/selector click ended up one whole window origin away.
    """
    detect = lambda hwnd: [_element("确定", "Button", x=40, y=40)]  # noqa: E731

    scene = scene_api.describe_window(
        4242, source="uia", include_ocr=False, include_shapes=False,
        capture=lambda hwnd: pytest.fail("a uia-only read needs no frame"),
        detect=detect, info=_info_at([59, 80, 1372, 962]),
    )

    element = scene["elements"][0]
    assert element["bbox"] == {"x": 40, "y": 40, "w": 80, "h": 30}
    assert element["center"] == {"x": 80, "y": 55}
    assert scene["window"]["rect"] == [59, 80, 1372, 962]
    assert scene["window"]["size"] == [1313, 882]
    assert scene["capture"]["size"] is None
    assert scene["capture"]["verified"] is None


def test_a_frame_with_another_pixel_density_is_resampled_into_window_pixels():
    """``window.size`` stays the window's grid, whatever the frame measured.

    A 1200x900 PrintWindow frame over an 800x600 window is the same page at
    1.5x.  Tree boxes are already window pixels and must not move; frame
    boxes (OCR / OpenCV) are resampled by 800/1200 so both land on the same
    control - the one grid ``eyes click`` can use.
    """
    detect = lambda hwnd: [_element("确定", "Button", x=200, y=160)]  # noqa: E731
    engine = _FakeEngine([[_poly(300, 240, 420, 300), "确定", 0.9]])

    scene = scene_api.describe_window(
        4242, source="auto", include_shapes=False,
        capture=lambda hwnd: _frame(1200, 900), detect=detect, engine=engine,
        info=_info_at([100, 60, 900, 660], size=[800, 600], dpi=1.5),
    )

    assert scene["window"]["size"] == [800, 600]
    assert scene["capture"]["size"] == [1200, 900]
    element = scene["elements"][0]
    assert element["bbox"] == {"x": 200, "y": 160, "w": 80, "h": 30}
    # The OCR copy of the same button is scaled into the same place and is
    # therefore deduplicated, not reported at 1.5x its true position.
    assert scene["text_lines"] == []
    assert [e["source"] for e in scene["elements"]] == ["uia"]


def test_a_uia_element_outside_the_window_is_not_an_executable_position():
    detect = lambda hwnd: [  # noqa: E731
        _element("可见", "Button", x=79, y=100),
        _element("越界", "Button", x=1674, y=200),
        _element("跨左边缘", "Button", x=-40, y=200),
    ]

    scene = scene_api.describe_window(
        4242, source="uia", include_ocr=False, include_shapes=False,
        detect=detect, info=_info_at([59, 80, 1372, 962]),
    )

    assert [element["text"] for element in scene["elements"]] == ["可见", "跨左边缘"]
    assert scene["elements"][0]["bbox"] == {"x": 79, "y": 100, "w": 80, "h": 30}
    # The box that starts left of the window is clipped, never left hanging.
    straddling = scene["elements"][1]
    assert straddling["bbox"] == {"x": 0, "y": 200, "w": 40, "h": 30}
    for element in scene["elements"]:
        assert element["bbox"]["x"] >= 0
        assert element["bbox"]["x"] + element["bbox"]["w"] <= 1313
        assert 0 <= element["center"]["x"] < 1313
        assert 0 <= element["center"]["y"] < 882
    assert any("uia-outside-window" in note for note in scene["notes"])


def test_source_uia_never_touches_the_ocr_engine():
    reads: list[str] = []

    class _Engine:
        def __call__(self, image):
            reads.append("ocr")
            return []

    scene = _describe(None, engine=_Engine(),
                      detect=lambda hwnd: [_element("确定")], source="uia")

    assert reads == []
    assert scene["source"] == "uia"
    assert scene["requested_source"] == "uia"
    assert scene["elements"][0]["text"] == "确定"
    assert not any("ocr" in note for note in scene["notes"])


def test_a_blank_printwindow_frame_does_not_send_a_uia_read_to_the_screen(
        monkeypatch):
    """`--source uia` stays a UIA read even when PrintWindow is empty."""
    reads: list[str] = []
    grabs: list[str] = []
    blank = _frame()

    class _Engine:
        def __call__(self, image):
            reads.append("ocr")
            return []

    def grab_printwindow(hwnd, capture=None):
        grabs.append("printwindow")
        return blank, "printwindow"

    def grab_screen(hwnd):
        grabs.append("screen")
        return blank, "screen"

    monkeypatch.setattr(scene_api, "_capture", grab_printwindow)
    monkeypatch.setattr(scene_api, "_capture_screen", grab_screen)
    monkeypatch.setattr(scene_api, "_on_screen", lambda hwnd: True)

    scene = scene_api.describe_window(
        4242, source="uia", capture=None, engine=_Engine(), info=_info,
        detect=lambda hwnd: [_element("确定")],
    )

    assert reads == []
    assert grabs == []                       # a uia read takes no frame at all
    assert scene["source"] == "uia"
    assert not any("fell back to a screen grab" in note for note in scene["notes"])
    assert any(note.startswith("shapes-skipped") for note in scene["notes"])


def test_uia_with_no_shapes_captures_nothing():
    """`--source uia --no-shapes`: the tree is the only thing read."""
    calls: list[str] = []

    def capture(hwnd):
        calls.append("capture")
        return _frame()

    scene = _describe(None, capture=capture, source="uia", include_shapes=False,
                      detect=lambda hwnd: [_element("确定")])

    assert calls == []
    assert scene["ok"] is True
    assert scene["elements"][0]["text"] == "确定"
    assert scene["capture"]["method"] is None
    assert scene["capture"]["size"] is None
    assert scene["shapes"] == []


def test_uia_with_shapes_requested_still_captures_nothing():
    """Visual features have to be asked for through a pixel tier.

    ``source="uia"`` is the accessibility tree only, even when the caller
    left ``include_shapes`` at its default: a uia read grabs no frame and
    reports no OpenCV contours, and the receipt says why.
    """
    calls: list[str] = []

    def capture(hwnd):
        calls.append("capture")
        return _frame()

    scene = _describe(None, capture=capture, source="uia", include_shapes=True,
                      detect=lambda hwnd: [_element("确定")])

    assert calls == []
    assert scene["capture"]["method"] is None
    assert scene["capture"]["size"] is None
    assert scene["shapes"] == []
    assert scene["ok"] is True
    assert any(note.startswith("shapes-skipped") for note in scene["notes"])


def test_auto_takes_the_frame_the_shapes_need():
    calls: list[str] = []

    def capture(hwnd):
        calls.append("capture")
        return _frame()

    scene = _describe(None, capture=capture, source="auto", include_ocr=False,
                      include_shapes=True, detect=lambda hwnd: [_element("确定")])

    assert calls == ["capture"]
    assert scene["capture"]["method"] == "injected"
    assert scene["capture"]["size"] == [400, 200]


def test_source_ocr_does_not_claim_a_fallback_it_never_took(monkeypatch):
    """`--source ocr` is pixels only: a missing engine is a failure, not a swerve."""
    class _Broken:
        def __call__(self, image):
            raise RuntimeError("no engine")

    enumerated: list[str] = []
    grabs: list[str] = []
    blank = _frame()

    def detect(hwnd):
        enumerated.append("uia")
        return [_element("确定")]

    def grab_printwindow(hwnd, capture=None):
        grabs.append("printwindow")
        return blank, "printwindow"

    def grab_screen(hwnd):
        grabs.append("screen")
        return blank, "screen"

    monkeypatch.setattr(scene_api, "_capture", grab_printwindow)
    monkeypatch.setattr(scene_api, "_capture_screen", grab_screen)
    monkeypatch.setattr(scene_api, "_on_screen", lambda hwnd: True)

    scene = scene_api.describe_window(
        4242, source="ocr", capture=None, engine=_Broken(), info=_info,
        detect=detect,
    )

    assert enumerated == []
    assert grabs == ["printwindow"]          # no second grab for a dead engine
    assert scene["elements"] == []
    assert scene["ok"] is False
    assert scene["error_code"] == "OCR_UNAVAILABLE"
    assert any(note.startswith("ocr-unavailable") for note in scene["notes"])
    assert not any("fell back to the accessibility tree" in note
                   for note in scene["notes"])


def test_auto_still_falls_back_to_the_screen_when_printwindow_carries_no_text(
        monkeypatch):
    """The guard above must not silence the fallback `auto` relies on."""
    blank = _frame()
    grabs: list[str] = []

    class _Engine:
        def __init__(self):
            self.calls = 0

        def __call__(self, image):
            self.calls += 1
            if self.calls == 1:
                return []
            return [[_poly(20, 10, 60, 30), "屏幕文字", 0.9]]

    def grab_printwindow(hwnd, capture=None):
        grabs.append("printwindow")
        return blank, "printwindow"

    def grab_screen(hwnd):
        grabs.append("screen")
        return blank, "screen"

    monkeypatch.setattr(scene_api, "_capture", grab_printwindow)
    monkeypatch.setattr(scene_api, "_capture_screen", grab_screen)
    monkeypatch.setattr(scene_api, "_on_screen", lambda hwnd: True)
    monkeypatch.setattr(scene_api, "_is_foreground", lambda hwnd: True)
    monkeypatch.setattr(scene_api, "_screen_view_clear", lambda hwnd: (True, None))

    scene = scene_api.describe_window(
        4242, capture=None, engine=_Engine(), info=_info,
        detect=lambda hwnd: [],
    )

    assert grabs == ["printwindow", "screen"]
    assert "屏幕文字" in scene["text_lines"]
    assert scene["capture"]["method"] == "screen"
    assert scene["capture"]["occluded"] is False
    assert scene["capture"]["verified"] is True
    assert any("fell back to a screen grab" in note for note in scene["notes"])


def test_screen_fallback_frame_is_checked_before_ocr(monkeypatch):
    blank = _frame()
    grabs: list[str] = []
    readability_checks: list[str] = []
    engine_reads: list[str] = []

    class _Engine:
        def __call__(self, image):
            engine_reads.append("ocr")
            return ([[_poly(20, 10, 60, 30), "屏幕文字", 0.9]]
                    if len(engine_reads) > 1 else [])

    def grab_screen(hwnd):
        grabs.append("screen")
        return blank, "screen"

    def view_clear(hwnd):
        readability_checks.append("check")
        if len(readability_checks) == 1:
            return True, None
        return False, "window became covered after the grab"

    monkeypatch.setattr(
        scene_api, "_capture",
        lambda hwnd, capture=None: (blank, "printwindow"),
    )
    monkeypatch.setattr(scene_api, "_capture_screen", grab_screen)
    monkeypatch.setattr(scene_api, "_on_screen", lambda hwnd: True)
    monkeypatch.setattr(scene_api, "_is_foreground", lambda hwnd: True)
    monkeypatch.setattr(scene_api, "_screen_view_clear", view_clear)

    scene = scene_api.describe_window(
        4242, capture=None, engine=_Engine(), info=_info,
        detect=lambda hwnd: [],
    )

    assert grabs == ["screen"]
    assert engine_reads == ["ocr"]
    assert scene["text_lines"] == []
    assert scene["capture"]["method"] == "printwindow"
    assert any("discarded before OCR" in note for note in scene["notes"])


# ------------------------------------------- pixels must be this window's own
def test_a_screen_grab_that_cannot_be_proven_is_never_read(monkeypatch):
    """A screen grab of a background window may hold another window's page.

    Turning those pixels into this window's text would report a foreign page
    as if it were the target, so the frame is kept (for provenance) and its
    OCR/OpenCV results are suppressed.
    """
    reads: list[str] = []

    class _Engine:
        def __call__(self, image):
            reads.append("ocr")
            return [[_poly(20, 10, 60, 30), "别的窗口的文字", 0.9]]

    monkeypatch.setattr(scene_api, "_capture",
                        lambda hwnd, capture=None: (_frame(), "screen"))
    monkeypatch.setattr(scene_api, "_on_screen", lambda hwnd: True)
    monkeypatch.setattr(scene_api, "_is_foreground", lambda hwnd: False)

    scene = scene_api.describe_window(
        4242, engine=_Engine(), info=_info, detect=lambda hwnd: [])

    assert reads == []
    assert scene["elements"] == []
    assert scene["text_lines"] == []
    assert scene["ok"] is False
    assert scene["error_code"] == "UNVERIFIED_FRAME"
    assert scene["capture"]["method"] == "screen"
    assert scene["capture"]["occluded"] is True
    assert scene["capture"]["verified"] is False
    assert scene["capture"]["suppressed"] == ["ocr", "opencv"]
    assert any("unverified-frame" in note for note in scene["notes"])


def test_a_foreground_screen_grab_is_read_and_marked_trustworthy(monkeypatch):
    monkeypatch.setattr(scene_api, "_capture",
                        lambda hwnd, capture=None: (_frame(), "screen"))
    monkeypatch.setattr(scene_api, "_on_screen", lambda hwnd: True)
    monkeypatch.setattr(scene_api, "_is_foreground", lambda hwnd: True)
    monkeypatch.setattr(scene_api, "_screen_view_clear", lambda hwnd: (True, None))

    engine = _FakeEngine([[_poly(20, 10, 60, 30), "本窗口文字", 0.9]])
    scene = scene_api.describe_window(
        4242, engine=engine, info=_info, detect=lambda hwnd: [])

    assert scene["text_lines"] == ["本窗口文字"]
    assert scene["capture"]["occluded"] is False
    assert scene["capture"]["verified"] is True
    assert scene["capture"]["suppressed"] == []


def test_being_foreground_does_not_make_a_partly_covered_grab_trustworthy(
        monkeypatch):
    """A foreground window can still be covered by an always-on-top window."""
    reads: list[str] = []

    class _Engine:
        def __call__(self, image):
            reads.append("ocr")
            return [[_poly(20, 10, 60, 30), "别处的内容", 0.9]]

    monkeypatch.setattr(scene_api, "_capture",
                        lambda hwnd, capture=None: (_frame(), "screen"))
    monkeypatch.setattr(scene_api, "_on_screen", lambda hwnd: True)
    monkeypatch.setattr(scene_api, "_is_foreground", lambda hwnd: True)
    monkeypatch.setattr(
        scene_api, "_screen_view_clear",
        lambda hwnd: (False, "screen point (40,20) belongs to hwnd=99, "
                             "not hwnd=4242"))

    scene = scene_api.describe_window(
        4242, engine=_Engine(), info=_info, detect=lambda hwnd: [])

    assert reads == []
    assert scene["text_lines"] == []
    assert scene["ok"] is False
    assert scene["error_code"] == "UNVERIFIED_FRAME"
    assert scene["capture"]["occluded"] is True
    assert scene["capture"]["verified"] is False
    assert scene["capture"]["suppressed"] == ["ocr", "opencv"]
    assert any("not fully visible" in note for note in scene["notes"])


def test_the_auto_screen_fallback_refuses_a_covered_window(monkeypatch):
    """The fallback must not turn another window's pixels into this text."""
    grabs: list[str] = []

    def grab_screen(hwnd):
        grabs.append("screen")
        return _frame(), "screen"

    class _Engine:
        def __call__(self, image):
            return []

    monkeypatch.setattr(scene_api, "_capture",
                        lambda hwnd, capture=None: (_frame(), "printwindow"))
    monkeypatch.setattr(scene_api, "_capture_screen", grab_screen)
    monkeypatch.setattr(scene_api, "_on_screen", lambda hwnd: True)
    monkeypatch.setattr(scene_api, "_is_foreground", lambda hwnd: True)
    monkeypatch.setattr(
        scene_api, "_screen_view_clear",
        lambda hwnd: (False, "screen point (10,10) belongs to hwnd=99"))

    scene = scene_api.describe_window(
        4242, capture=None, engine=_Engine(), info=_info,
        detect=lambda hwnd: [])

    assert grabs == []
    assert scene["capture"]["method"] == "printwindow"
    assert any("screen fallback skipped" in note for note in scene["notes"])


def test_a_printwindow_frame_needs_no_foreground_to_be_trusted(monkeypatch):
    """PrintWindow makes the window paint itself: occlusion is irrelevant."""
    monkeypatch.setattr(scene_api, "_capture",
                        lambda hwnd, capture=None: (_frame(), "printwindow"))
    monkeypatch.setattr(scene_api, "_on_screen", lambda hwnd: True)
    monkeypatch.setattr(scene_api, "_is_foreground", lambda hwnd: False)

    engine = _FakeEngine([[_poly(20, 10, 60, 30), "后台文字", 0.9]])
    scene = scene_api.describe_window(
        4242, engine=engine, info=_info, detect=lambda hwnd: [])

    assert scene["text_lines"] == ["后台文字"]
    assert scene["capture"]["verified"] is True


def test_opencv_shapes_are_evidence_not_interactive_controls(monkeypatch):
    """A contour proves geometry, not clickability."""
    monkeypatch.setattr(scene_api, "_detect_shapes",
                        lambda frame, **_kwargs: [{"kind": "checkbox",
                                                   "bbox": {"x": 20, "y": 10,
                                                            "w": 16, "h": 16}}])

    scene = scene_api.describe_window(
        4242, source="auto", include_ocr=False, include_shapes=True,
        capture=lambda hwnd: _frame(), detect=lambda hwnd: [], info=_info)

    shape = scene["elements"][0]
    assert shape["source"] == "opencv"
    assert shape["interactive"] is False


# --------------------------------------------------------- parameter receipts
def test_an_unknown_source_is_a_structured_failure():
    scene = scene_api.describe_window(4242, source="pixels", info=_info)

    assert scene["ok"] is False
    assert scene["error_code"] == "INVALID_SOURCE"
    assert "auto" in scene["reason"]
    assert scene["requested_source"] == "pixels"


def test_a_malformed_region_is_a_structured_failure():
    scene = scene_api.describe_window(4242, region=(300, 150, 100, 50),
                                      info=_info)

    assert scene["ok"] is False
    assert scene["error_code"] == "INVALID_REGION"
    assert scene["reason"]


def test_a_region_outside_the_frame_is_a_structured_failure():
    scene = scene_api.describe_window(
        4242, region=(1000, 1000, 1100, 1100), info=_info,
        capture=lambda hwnd: _frame(400, 200), detect=lambda hwnd: [])

    assert scene["ok"] is False
    assert scene["error_code"] == "INVALID_REGION"
    assert any("region-outside-frame" in note for note in scene["notes"])


def test_a_capture_that_returns_nothing_is_a_capture_failure():
    scene = scene_api.describe_window(
        4242, capture=lambda hwnd: None, info=_info, detect=lambda hwnd: [])

    assert scene["ok"] is False
    assert scene["error_code"] == "CAPTURE_FAILED"
    assert any("capture-failed" in note for note in scene["notes"])


def test_a_crop_exception_does_not_escape_the_scene():
    class _BadFrame:
        width = 400
        height = 200

        def crop(self, box):
            raise RuntimeError("crop broke")

    scene = scene_api.describe_window(
        4242, region=(0, 0, 10, 10), capture=lambda hwnd: _BadFrame(),
        info=_info, detect=lambda hwnd: [], engine=None)

    assert scene["ok"] is False
    assert scene["error_code"] == "CAPTURE_FAILED"
    assert any("crop-failed" in note for note in scene["notes"])


def test_an_empty_tree_and_a_filtered_scene_fail_differently():
    """An empty tree and a tree whose every node was filtered are not the same."""
    empty = scene_api.describe_window(
        4242, source="uia", include_ocr=False, include_shapes=False,
        detect=lambda hwnd: [], info=_info)

    filtered = scene_api.describe_window(
        4242, source="uia", include_ocr=False, include_shapes=False,
        detect=lambda hwnd: [_element("ContentsView", "Pane")], info=_info)

    assert empty["error_code"] == "UIA_EMPTY"
    assert empty["ok"] is False
    assert filtered["error_code"] == "SCENE_FILTERED_EMPTY"
    assert filtered["ok"] is False


# ------------------------------------------------- occlusion probe, fail closed
class _FakeWin32Gui:
    """A win32gui stand-in: a window rect plus who owns each point."""

    def __init__(self, rect, owner=None, fail_rect=False, fail_hit=False):
        self.rect = rect
        self.owner = owner or (lambda point: 4242)
        self.fail_rect = fail_rect
        self.fail_hit = fail_hit

    def GetWindowRect(self, hwnd):
        if self.fail_rect:
            raise OSError("GetWindowRect failed")
        return self.rect

    def WindowFromPoint(self, point):
        if self.fail_hit:
            raise OSError("WindowFromPoint failed")
        return self.owner(point)

    def GetAncestor(self, hwnd, flag):
        return hwnd


def _with_win32gui(monkeypatch, fake) -> None:
    monkeypatch.setitem(sys.modules, "win32gui", fake)


def test_the_occlusion_probe_accepts_a_window_that_owns_every_sample(monkeypatch):
    _with_win32gui(monkeypatch, _FakeWin32Gui((0, 0, 400, 200)))

    assert scene_api._screen_view_clear(4242) == (True, None)


def test_the_occlusion_probe_names_the_covering_window(monkeypatch):
    def owner(point):
        return 99 if point[0] > 200 else 4242

    _with_win32gui(monkeypatch, _FakeWin32Gui((0, 0, 400, 200), owner))

    clear, reason = scene_api._screen_view_clear(4242)

    assert clear is False
    assert "hwnd=99" in reason and "hwnd=4242" in reason


def test_the_occlusion_probe_cannot_run_is_not_clear(monkeypatch):
    """Fail closed: no evidence of a clear view is not evidence of one."""
    _with_win32gui(monkeypatch, _FakeWin32Gui((0, 0, 400, 200), fail_rect=True))
    assert scene_api._screen_view_clear(4242)[0] is False

    _with_win32gui(monkeypatch, _FakeWin32Gui((0, 0, 400, 200), fail_hit=True))
    assert scene_api._screen_view_clear(4242)[0] is False

    _with_win32gui(monkeypatch, _FakeWin32Gui((10, 10, 10, 200)))
    assert scene_api._screen_view_clear(4242)[0] is False


def test_a_window_rectangle_without_area_is_not_readable():
    def info(hwnd):
        return {"title": "x", "class_name": "y", "rect": [10, 10, 10, 200],
                "size": [0, 190], "dpi_scale": 1.0}

    scene = scene_api.describe_window(
        4242, source="uia", include_ocr=False, include_shapes=False,
        detect=lambda hwnd: [], info=info)

    assert scene["ok"] is False
    assert scene["error_code"] == "INVALID_HWND"
    assert "no area" in scene["reason"]


def test_an_unreadable_tree_is_a_structured_failure():
    def detect(hwnd):
        raise RuntimeError("UIA is not available")

    scene = scene_api.describe_window(
        4242, source="uia", include_ocr=False, include_shapes=False,
        detect=detect, info=_info)

    assert scene["ok"] is False
    assert scene["error_code"] == "UIA_UNAVAILABLE"
    assert "UIA is not available" in scene["reason"]


# ------------------------------------------- shape scale, round 4 review
#: A frame 1.5x larger than the window: ``window / frame`` per axis.
_FRAME_1_5X = (400 / 600, 200 / 300)


def test_shape_scale_keeps_the_frame_correction_without_a_dpi_read():
    """An unreadable DPI means "assume 96 DPI", not "frame == window".

    ``frame_scale`` is measured independently of ``dpi_scale``; dropping it
    applies design-pixel thresholds to a frame that is 1.5x larger, which
    shrinks every limit against pixels that are already scaled up.
    """
    for window in ({"dpi_scale": None}, {}, {"dpi_scale": 0},
                   {"dpi_scale": "bad"}, {"dpi_scale": float("nan")},
                   {"dpi_scale": float("inf")}):
        assert scene_api.shape_scale(window, _FRAME_1_5X) == pytest.approx(1.5)

    # Still 1.0 when the frame really is the window grid, and 1.0 with no
    # frame at all - the fallback is the frame correction, not a constant.
    assert scene_api.shape_scale({}, (1.0, 1.0)) == 1.0
    assert scene_api.shape_scale({"dpi_scale": None}) == 1.0
    # A good DPI read is still multiplied by the same correction.
    assert scene_api.shape_scale({"dpi_scale": 2.0}, (2.0, 2.0)) == 1.0


def test_a_bigger_frame_without_a_dpi_read_still_finds_a_checkbox():
    """The frame correction alone has to keep the checkbox threshold usable."""
    pytest.importorskip("cv2")

    def shape_at(dpi):
        image = Image.new("RGB", (600, 300), (18, 18, 18))
        ImageDraw.Draw(image).rectangle([300, 150, 323, 173],
                                        fill=(230, 230, 230))
        return scene_api.describe_window(
            4242, source="auto", include_ocr=False, include_shapes=True,
            capture=lambda hwnd: image, detect=lambda hwnd: [],
            info=_info_at([0, 0, 400, 200], size=[400, 200], dpi=dpi))

    known = shape_at(1.0)
    unknown = shape_at(None)

    # 24x24 frame px at 1.5x is a 16x16 design-pixel box: a checkbox under
    # the corrected thresholds, a panel under the uncorrected 400 px one.
    assert "checkbox" in {shape["kind"] for shape in known["shapes"]}
    assert "checkbox" in {shape["kind"] for shape in unknown["shapes"]}
    assert any(note.startswith("shape-scale:") for note in unknown["notes"])


def test_coverage_fallback_without_numpy_matches_the_mask_path():
    """The ImportError branch must agree with the mask path, not raise.

    It is a shifted pairwise sweep (``x_edges`` vs ``x_edges[1:]``), so the
    two iterables differ by one by construction: ``zip(..., strict=True)``
    raised ``ValueError`` on every non-empty call.
    """
    box = [0, 0, 10, 10]
    cases = [
        ([[0, 0, 10, 5], [0, 5, 10, 5]], 1.0),     # two disjoint halves
        ([[0, 0, 10, 5], [0, 0, 10, 5]], 0.5),     # the same half twice
        ([[0, 0, 10, 5], [0, 0, 5, 10]], 0.75),    # 50% + 50% - 25% overlap
        ([[100, 100, 5, 5]], 0.0),                 # nothing inside
    ]
    expected = [scene_api._coverage(box, others) for others, _ in cases]

    with pytest.MonkeyPatch.context() as patch:
        patch.setitem(sys.modules, "numpy", None)
        with pytest.raises(ImportError):
            __import__("numpy")
        fallback = [scene_api._coverage(box, others) for others, _ in cases]

    assert fallback == expected
    assert fallback == [value for _, value in cases]


def test_a_caret_inside_a_text_line_is_the_same_ink_reported_once(monkeypatch):
    """The shape/OCR dedup runs shape -> text: a caret inside a line is a glyph."""
    engine = _FakeEngine([[_poly(10, 10, 90, 40), "v1", 0.9]])
    caret = {"kind": "caret", "bbox": {"x": 20, "y": 15, "w": 10, "h": 10}}
    monkeypatch.setattr(scene_api, "_detect_shapes",
                        lambda frame, **_kwargs: [dict(caret)])

    inside = scene_api.describe_window(
        4242, source="auto", capture=lambda hwnd: _frame(), engine=engine,
        detect=lambda hwnd: [], info=_info)

    assert "caret" not in {element["kind"] for element in inside["elements"]}
    assert inside["text_lines"] == ["v1"]

    away = {"kind": "caret", "bbox": {"x": 200, "y": 15, "w": 10, "h": 10}}
    monkeypatch.setattr(scene_api, "_detect_shapes",
                        lambda frame, **_kwargs: [dict(away)])

    outside = scene_api.describe_window(
        4242, source="auto", capture=lambda hwnd: _frame(), engine=engine,
        detect=lambda hwnd: [], info=_info)

    assert [element["kind"] for element in outside["elements"]].count("caret") == 1
