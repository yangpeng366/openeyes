"""Pure helpers of ``examples/record_demo_expect.py``.

The script itself needs a live window; these functions decide *what* it
records, so they are tested without touching the desktop.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "record_demo_expect.py"


def _module():
    spec = importlib.util.spec_from_file_location("record_demo_expect", EXAMPLE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _elem(control_type="", name="", automation_id="", interactive=True):
    return SimpleNamespace(control_type=control_type, name=name,
                           automation_id=automation_id, interactive=interactive)


def test_choose_expect_prefers_a_plain_label():
    demo = _module()
    assert demo.choose_expect(["“新建 Markdown”选项卡", "保存", "全部保存"]) == "保存"


def test_choose_expect_falls_back_to_whatever_was_revealed():
    demo = _module()
    assert demo.choose_expect(["“新建”选项卡"]) == "“新建”选项卡"


def test_choose_expect_without_revealed_names_returns_empty():
    demo = _module()
    assert demo.choose_expect([]) == ""


def test_pick_anchor_prefers_a_named_menu_item():
    demo = _module()
    elements = [
        _elem("MenuBar", "", "MenuBar"),
        _elem("MenuItem", "文件", "File"),
    ]
    assert demo.pick_anchor(elements).automation_id == "File"


def test_pick_anchor_skips_unnamed_menubar():
    demo = _module()
    elements = [
        _elem("MenuBar", "", "MenuBar"),
        _elem("Button", "确定", "okButton"),
    ]
    assert demo.pick_anchor(elements).name == "确定"


def test_pick_anchor_uses_an_addressable_interactive_control():
    demo = _module()
    elements = [
        _elem("Text", "label", "", interactive=False),
        _elem("Button", "保存", "saveBtn"),
    ]
    assert demo.pick_anchor(elements).automation_id == "saveBtn"


def test_pick_anchor_returns_none_when_nothing_is_addressable():
    demo = _module()
    assert demo.pick_anchor([_elem("Text", "", "")]) is None


def test_names_drops_blank_labels():
    demo = _module()
    elements = [_elem(name=" 保存 "), _elem(name=""), _elem(name="   ")]
    assert demo._names(elements) == {"保存"}
