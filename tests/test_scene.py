"""Scene building: what an agent gets to read about a window.

Synthetic frames and fake engines only - no real window, no OCR package.
"""
from __future__ import annotations

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
