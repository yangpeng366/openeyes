"""Smoke tests - verify imports + cross-platform safe ops without clicking."""
from __future__ import annotations
import pytest

import openeyes
from openeyes import (
    BBox, Center, Element, ElementState, WindowInfo,
    list_windows, find_window, find_elements,
)
from openeyes.core.schema import BBox as BBox2  # alias check


def test_version():
    assert openeyes.__version__ == "0.1.0"


def test_schema_dataclasses_roundtrip():
    e = Element(
        backend="uia",
        control_type="Button",
        name="OK",
        bbox=BBox(x=10, y=20, w=80, h=30),
        center=Center(x=50, y=35),
    )
    d = e.to_dict()
    assert d["backend"] == "uia"
    assert d["control_type"] == "Button"
    assert d["bbox"]["x"] == 10
    assert d["center"]["x"] == 50


def test_list_windows_returns_at_least_one_or_empty():
    wins = list_windows()
    assert isinstance(wins, list)
    for w in wins:
        assert isinstance(w, WindowInfo)
        assert w.title
        assert w.w > 0
        assert w.h > 0


def test_find_window_no_match_returns_empty():
    wins = find_window(title_contains="__definitely_not_a_window__")
    assert wins == []


def test_find_elements_filters():
    elems = [
        Element(backend="uia", control_type="Button", name="Submit",
                bbox=BBox(0, 0, 10, 10), center=Center(5, 5)),
        Element(backend="uia", control_type="Hyperlink", name="Cancel",
                bbox=BBox(0, 0, 10, 10), center=Center(5, 5)),
        Element(backend="uia", control_type="Button", name="OK",
                bbox=BBox(0, 0, 10, 10), center=Center(5, 5)),
    ]
    buttons = find_elements(elems, control_type="Button")
    assert len(buttons) == 2
    submit = find_elements(elems, name_contains="Sub")
    assert len(submit) == 1 and submit[0].name == "Submit"
    exact = find_elements(elems, control_type="Button", name_contains="OK")
    assert len(exact) == 1 and exact[0].name == "OK"


def test_element_state_defaults():
    s = ElementState()
    assert s.enabled and s.visible and not s.focused and not s.selected


@pytest.mark.parametrize("platform", ["win32", "darwin", "linux"])
def test_platform_backend_resolves_or_raises(monkeypatch, platform):
    """Backend dispatch handles unknown / unsupported platforms cleanly."""
    import sys
    monkeypatch.setattr(sys, "platform", platform)
    if platform in ("darwin", "linux"):
        from openeyes.core import windows
        with pytest.raises(NotImplementedError):
            windows.list_windows()
    else:
        from openeyes.core import windows
        wins = windows.list_windows()
        assert isinstance(wins, list)


def test_focus_window_returns_bool():
    """focus_window must return a bool so callers can branch on success."""
    from openeyes.actuators.win32 import focus_window
    wins = list_windows()
    if not wins:
        result = focus_window(0)
        assert isinstance(result, bool)
    else:
        result = focus_window(wins[0].hwnd)
        assert isinstance(result, bool)


def test_keepalive_targets_are_keepalive_only():
    """KEEPALIVE_TARGETS must not include Secure my connection by default.

    Guards against the auto-reconnect surprise: clicking Secure my connection
    changes network routing, which is out of scope for a keepalive loop.
    Bootstrap must be opt-in.
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "anyvpn_keepalive_test",
        r"E:\gitAll\openeyes\examples\anyvpn_keepalive.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    joined = " ".join(mod.KEEPALIVE_TARGETS).lower()
    assert "secure my connection" not in joined, (
        "KEEPALIVE_TARGETS must not auto-connect; use BOOTSTRAP_TARGETS + --bootstrap"
    )


def test_status_command_exists():
    """eyes status subcommand should be a callable Click command."""
    from openeyes.cli.main import status
    assert callable(status)