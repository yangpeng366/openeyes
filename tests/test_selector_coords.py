"""``click_by_selector`` must cross the same coordinate gap as the CLI.

``Element.center`` from the UIA backend is measured *inside* the window,
while ``openeyes.core.actuator.click_xy`` speaks absolute screen pixels.
The CLI learned this first (``tests/test_coords.py``); ``click_by_selector``
is the library-level twin and used to hand the window-relative centre to
``click_xy`` unchanged, so a window at (300, 200) made every "successful"
click land (300, 200) away from the control.

The last test pins the WebDriver BiDi backend: it measures DOM boxes in the
page viewport, exactly like CDP, so it is registered as a viewport backend
rather than left to raise ``UnknownCoordinateSpace``.
"""
from __future__ import annotations

import sys
import types

import pytest

from openeyes.core.coords import (
    VIEWPORT,
    MissingViewportOrigin,
    element_space,
    to_screen_point,
)
from openeyes.core.schema import BBox, Center, Element
from openeyes.core.selector import click_by_selector


def _fake_win32gui(origin: tuple[int, int]) -> types.SimpleNamespace:
    """A stand-in for win32gui whose window sits at *origin*."""
    left, top = origin

    def get_window_rect(hwnd):
        return left, top, left + 800, top + 600

    return types.SimpleNamespace(GetWindowRect=get_window_rect)


def _element(backend: str = "uia", x: int = 10, y: int = 20,
             name: str = "Save") -> Element:
    return Element(backend=backend, control_type="Button", name=name,
                   bbox=BBox(x - 5, y - 5, 10, 10), center=Center(x, y),
                   automation_id="SaveButton")


def _patch_detect(monkeypatch, elements: list[Element]) -> None:
    """Return *elements* from the selector's own ``detect_elements``."""
    monkeypatch.setattr("openeyes.core.selector.detect_elements",
                        lambda *a, **k: list(elements))


def test_a_selector_click_adds_the_window_origin(monkeypatch):
    """An element centred at (10, 20) in a window at (300, 200) is clicked
    at (310, 220) on screen - not at (10, 20)."""
    monkeypatch.setitem(sys.modules, "win32gui", _fake_win32gui((300, 200)))
    _patch_detect(monkeypatch, [_element("uia", 10, 20)])
    clicked: list[tuple[int, int]] = []
    monkeypatch.setattr("openeyes.core.actuator.click_xy",
                        lambda x, y, **k: clicked.append((int(x), int(y))))

    target = click_by_selector(7, name_contains="Save", dry_run=False)

    assert target.name == "Save"
    assert clicked == [(310, 220)]


def test_a_dry_run_does_not_click_and_returns_the_same_element(monkeypatch):
    """Dry-run stays a pure read: no window rectangle, no pointer."""
    element = _element("uia", 10, 20)
    _patch_detect(monkeypatch, [element])

    def fail(*a, **k):
        raise AssertionError("a dry run must not click")

    monkeypatch.setattr("openeyes.core.actuator.click_xy", fail)
    monkeypatch.setattr("openeyes.core.selector._window_origin", fail,
                        raising=False)

    assert click_by_selector(7, name_contains="Save", dry_run=True) is element


def test_bidi_is_a_viewport_backend():
    """BiDi reports DOM boxes in the viewport space, like CDP."""
    assert element_space(_element("bidi", 10, 20)) == VIEWPORT


def test_a_bidi_element_without_a_viewport_origin_raises():
    """No viewport origin: fail loudly, never click the raw DOM centre."""
    with pytest.raises(MissingViewportOrigin):
        to_screen_point(_element("bidi", 10, 20), window_origin=(300, 200))


def test_a_bidi_element_uses_the_viewport_origin():
    """The same pixel as CDP: (10, 20) + viewport (300, 200) = (310, 220)."""
    assert to_screen_point(_element("bidi", 10, 20),
                           viewport_origin=(300, 200)) == (310, 220)


class _Rect:
    """A pywinauto-shaped rectangle, in absolute screen pixels."""

    def __init__(self, left: int, top: int, width: int, height: int):
        self.left = left
        self.top = top
        self.width = width
        self.height = height


class _Node:
    """A pywinauto-shaped accessibility node."""

    def __init__(self, name: str, control_type: str, rect: _Rect,
                 children: list[_Node] | None = None):
        self.name = name
        self.control_type = control_type
        self.rectangle = rect
        self.automation_id = ""
        self.class_name = ""
        self.children = children or []


def _patch_backend(monkeypatch, origin: tuple[int, int], root: _Node) -> None:
    """Run the real uia backend against a fake tree at *origin*."""
    from openeyes.backends import uia as uia_backend

    class _FakeWindow:
        def __init__(self) -> None:
            self.element_info = root

    class _FakeDesktop:
        def __init__(self, backend: str | None = None) -> None:
            self.backend = backend

        def window(self, handle: int) -> _FakeWindow:
            return _FakeWindow()

    monkeypatch.setitem(sys.modules, "win32gui", _fake_win32gui(origin))
    monkeypatch.setattr(uia_backend, "Desktop", _FakeDesktop)
    monkeypatch.setattr(uia_backend, "user32", types.SimpleNamespace(
        ShowWindow=lambda *a, **k: 0))


def test_the_uia_backend_reports_window_relative_boxes(monkeypatch):
    """UIA measures absolute pixels; ``Element.bbox`` must be window-relative.

    A window at (300, 200) whose button sits at absolute (310, 220) is the
    button at (10, 20) inside the window - which is the space
    ``openeyes.core.coords`` and ``schema.Element`` both promise.
    """
    from openeyes.backends import uia as uia_backend

    _patch_backend(monkeypatch, (300, 200),
                   _Node("Save", "Button", _Rect(310, 220, 40, 20)))

    elements = uia_backend.detect_elements(7)

    assert [(e.name, e.bbox.x, e.bbox.y) for e in elements] == [("Save", 10, 20)]
    assert (elements[0].center.x, elements[0].center.y) == (30, 30)


def test_selector_click_through_the_real_backend_adds_the_origin_once(monkeypatch):
    """End to end: the tree says (10, 20), the click lands at (310, 220)."""
    _patch_backend(monkeypatch, (300, 200),
                   _Node("Save", "Button", _Rect(310, 220, 40, 20)))
    clicked: list[tuple[int, int]] = []
    monkeypatch.setattr("openeyes.core.actuator.click_xy",
                        lambda x, y, **k: clicked.append((int(x), int(y))))

    target = click_by_selector(7, name_contains="Save", dry_run=False)

    assert target.name == "Save"
    assert clicked == [(330, 230)]


def test_the_uia_backend_refuses_to_guess_a_window_origin(monkeypatch):
    """No rectangle: fail loudly, never subtract (0, 0) and call it relative."""
    from openeyes.backends import uia as uia_backend

    _patch_backend(monkeypatch, (300, 200),
                   _Node("Save", "Button", _Rect(310, 220, 40, 20)))

    def broken(hwnd):
        raise OSError("GetWindowRect failed")

    monkeypatch.setitem(sys.modules, "win32gui",
                        types.SimpleNamespace(GetWindowRect=broken))

    with pytest.raises(uia_backend.WindowOriginUnavailable):
        uia_backend.detect_elements(7)
