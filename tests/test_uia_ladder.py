"""Tests for the delivery ladder.

The ladder is a promise with two halves: the cheapest path that works
wins, and the user's pointer is only reached for when nothing else
does - and only when the budget says yes.

These tests drive the ladder directly with a stand-in wrapper, so they
say what the rules are without needing a real window.
"""
from __future__ import annotations

from types import SimpleNamespace

from openeyes.agent.profile import FocusBudget
from openeyes.backends import uia


class _Wrapper:
    """A stand-in for a UIA wrapper."""

    def __init__(self, *, invoke_ok=True, handle=0, control_type="Button"):
        self._invoke_ok = invoke_ok
        self.element_info = SimpleNamespace(
            handle=handle,
            name="Save",
            automation_id="SaveButton",
            control_type=control_type,
            rectangle=SimpleNamespace(left=0, top=0, right=10, bottom=10),
        )

    def invoke(self):
        if not self._invoke_ok:
            raise RuntimeError("no invoke pattern")


def _report():
    return {"ok": False, "method": None, "error": None, "error_code": None,
            "landed": None}


def _deliver(wrapper, **kwargs):
    options = {"method": "auto", "budget": None, "cx": 5, "cy": 5,
               "control_type": "Button", "press_ms": 0, "settle_ms": 0}
    options.update(kwargs)
    report = _report()
    uia._deliver(wrapper, 42, report=report, **options)
    return report


def _no_pointer(monkeypatch):
    """Make any attempt on the pointer a loud failure."""
    def fail(*args, **kwargs):
        raise AssertionError("the pointer must not be touched")

    monkeypatch.setattr("openeyes.backends.uia.click_xy", fail)
    return fail


# ------------------------------------------------------------------ the rungs
def test_an_invoke_never_reaches_for_the_pointer(monkeypatch):
    _no_pointer(monkeypatch)
    report = _deliver(_Wrapper(invoke_ok=True))
    assert report["ok"] is True
    assert report["method"] == "invoke"
    assert report["error_code"] is None


def test_a_failed_invoke_falls_to_the_window_message(monkeypatch):
    _no_pointer(monkeypatch)
    posted = []
    monkeypatch.setattr("win32gui.PostMessage",
                        lambda hwnd, msg, wparam, lparam: posted.append(hwnd))
    report = _deliver(_Wrapper(invoke_ok=False, handle=0x1234))
    assert report["ok"] is True
    assert report["method"] == "message"
    assert posted == [0x1234]


def test_the_message_rung_is_skipped_when_the_control_owns_no_window(monkeypatch):
    _no_pointer(monkeypatch)
    report = _deliver(_Wrapper(invoke_ok=False, handle=0))
    assert report["ok"] is False
    assert report["method"] is None


def test_the_pixel_rung_needs_a_budget(monkeypatch):
    """No budget, no pointer: the action fails instead of taking the mouse."""
    _no_pointer(monkeypatch)
    report = _deliver(_Wrapper(invoke_ok=False, handle=0),
                      budget=FocusBudget(0))
    assert report["ok"] is False
    assert report["error_code"] == "BACKGROUND_UNAVAILABLE"


def test_a_budget_lets_the_pixel_rung_run(monkeypatch):
    clicked = []
    monkeypatch.setattr("openeyes.backends.uia.click_xy",
                        lambda x, y, **kwargs: clicked.append((x, y)))
    monkeypatch.setattr("openeyes.actuators.win32.cursor_pos", lambda: (5, 5))
    budget = FocusBudget(1)
    report = _deliver(_Wrapper(invoke_ok=False, handle=0), budget=budget)
    assert report["ok"] is True
    assert report["method"] == "pixel"
    assert report["landed"] is True
    assert clicked == [(5, 5)]
    assert budget.used == 1


def test_an_exhausted_budget_says_so(monkeypatch):
    _no_pointer(monkeypatch)
    budget = FocusBudget(1)
    budget.spend()
    report = _deliver(_Wrapper(invoke_ok=False, handle=0), budget=budget)
    assert report["ok"] is False
    assert report["error_code"] == "BUDGET_EXCEEDED"


def test_an_explicit_pixel_request_ignores_the_budget(monkeypatch):
    """Asking for it is the authorisation; it is still counted."""
    clicked = []
    monkeypatch.setattr("openeyes.backends.uia.click_xy",
                        lambda x, y, **kwargs: clicked.append((x, y)))
    monkeypatch.setattr("openeyes.actuators.win32.cursor_pos", lambda: (5, 5))
    report = _deliver(_Wrapper(invoke_ok=False, handle=0), method="pixel",
                      budget=FocusBudget(0))
    assert report["ok"] is True
    assert report["method"] == "pixel"
    assert clicked == [(5, 5)]


def test_a_refused_delivery_says_no_semantic_path(monkeypatch):
    _no_pointer(monkeypatch)
    report = _deliver(_Wrapper(invoke_ok=False, handle=0), method="invoke")
    assert report["ok"] is False
    assert report["error_code"] == "NO_SEMANTIC_PATH"
    assert "invoke failed" in report["error"]


def test_the_foreground_rung_takes_the_window_to_the_front(monkeypatch):
    clicked = []
    monkeypatch.setattr("openeyes.backends.uia.click_xy",
                        lambda x, y, **kwargs: clicked.append((x, y)))
    monkeypatch.setattr("openeyes.actuators.win32.cursor_pos", lambda: (5, 5))
    focused = []
    monkeypatch.setattr("openeyes.actuators.win32.focus_window",
                        lambda hwnd: focused.append(hwnd) or True)
    report = _deliver(_Wrapper(invoke_ok=False, handle=0),
                      method="foreground")
    assert report["ok"] is True
    assert report["method"] == "foreground"
    assert focused == [42]
    assert clicked == [(5, 5)]
