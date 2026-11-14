"""Tests for the plain-text scene renderer."""
from __future__ import annotations

from typing import Any

from openeyes.core.scene_text import KIND_NAMES, render_scene

EXPECTED_KINDS = {
    "button": "按钮",
    "label": "文本",
    "input": "输入框",
    "checkbox": "复选框",
    "icon": "图标",
    "panel": "面板",
    "menu": "菜单",
    "unknown": "未知",
}


def _element(kind: str, text: str, x: int, y: int, w: int = 40, h: int = 20,
             score: Any = 0.99,
             center: dict[str, int] | None = None) -> dict[str, Any]:
    element: dict[str, Any] = {
        "kind": kind,
        "text": text,
        "bbox": {"x": x, "y": y, "w": w, "h": h},
        "score": score,
    }
    if center is not None:
        element["center"] = center
    return element


def _scene(*elements: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    scene: dict[str, Any] = {
        "source": "ocr",
        "window": {
            "title": "Trae CN",
            "class_name": "Chrome_WidgetWin_1",
            "size": [1886, 1183],
        },
        "elements": list(elements),
    }
    scene.update(overrides)
    return scene


def test_header_and_first_element_line_are_exact():
    button = _element("button", "新任务", 24, 143, w=106, h=48, score=0.99,
                      center={"x": 77, "y": 167})
    label = _element("label", "SOLO", 30, 90, w=34, h=16, score=0.99,
                     center={"x": 47, "y": 98})
    lines = render_scene(_scene(button, label)).splitlines()
    assert lines[0] == "窗口 Trae CN 1886x1183 来源=ocr 元素=2"
    assert lines[1] == "[文本] SOLO @(47,98) 0.99"
    assert lines[2] == "[按钮] 新任务 @(77,167) 0.99"


def test_kind_names_cover_all_eight_categories():
    assert KIND_NAMES == EXPECTED_KINDS
    elements = [_element(kind, "", 10, i * 30) for i, kind in
                enumerate(EXPECTED_KINDS)]
    out = render_scene(_scene(*elements))
    for name in EXPECTED_KINDS.values():
        assert f"[{name}]" in out


def test_unlisted_kind_is_rendered_verbatim():
    out = render_scene(_scene(_element("slider", "音量", 10, 10)))
    assert "[slider] 音量 @(30,20) 0.99" in out


def test_empty_text_omits_text_segment():
    icon = _element("icon", "", 1800, 1100, w=68, h=44, score=0.88,
                    center={"x": 1834, "y": 1122})
    lines = render_scene(_scene(icon)).splitlines()
    assert lines[1] == "[图标] @(1834,1122) 0.88"


def test_whitespace_only_text_is_treated_as_empty():
    icon = _element("icon", "   ", 10, 10, center={"x": 5, "y": 6})
    assert render_scene(_scene(icon)).splitlines()[1] == "[图标] @(5,6) 0.99"


def test_empty_scene_does_not_raise():
    assert render_scene({}) == "窗口 元素=0"


def test_missing_fields_fall_back_to_class_name_and_no_size():
    scene = {"window": {"class_name": "Notepad"}, "elements": None}
    assert render_scene(scene) == "窗口 Notepad 元素=0"


def test_elements_are_sorted_by_bbox_top_then_left():
    elements = [
        _element("label", "bottom", 10, 500),
        _element("label", "middle", 100, 100),
        _element("label", "top", 10, 10),
        _element("label", "top-right", 200, 10),
    ]
    lines = render_scene(_scene(*elements)).splitlines()[1:]
    assert [line.split()[1] for line in lines] == [
        "top", "top-right", "middle", "bottom",
    ]


def test_missing_center_uses_bbox_midpoint_and_two_decimal_score():
    element = {
        "kind": "button",
        "text": "Go",
        "bbox": {"x": 10, "y": 20, "w": 40, "h": 10},
        "score": 0.5,
    }
    out = render_scene({"window": {"size": [100, 100]}, "elements": [element]})
    assert out.splitlines()[1] == "[按钮] Go @(30,25) 0.50"


def test_truncation_by_max_lines_adds_ellipsis():
    elements = [_element("label", f"item{i}", 10, 10 + i * 20)
                for i in range(10)]
    lines = render_scene(_scene(*elements), max_lines=3).splitlines()
    assert len(lines) == 4                                  # header + 2 + note
    assert lines[0].startswith("窗口 ")
    assert "省略 8 行" in lines[-1]
    assert "字符" in lines[-1]


def test_truncation_by_max_chars_adds_ellipsis():
    elements = [_element("label", "x" * 20, 10, 10 + i * 20)
                for i in range(10)]
    lines = render_scene(_scene(*elements), max_chars=120).splitlines()
    assert len(lines) < 11
    assert "省略" in lines[-1]


def test_no_ellipsis_when_everything_fits():
    elements = [_element("label", f"item{i}", 10, 10 + i * 20)
                for i in range(3)]
    out = render_scene(_scene(*elements))
    assert "省略" not in out
    assert len(out.splitlines()) == 4
