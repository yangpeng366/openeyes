"""Contract tests for the act-then-prove MCP tools.

The two tools added here close the gap between the Python surface and the
MCP surface: a host could not address a control by its stable
``automation_id``, and could not ask "did the action actually take
effect?".
"""
from __future__ import annotations

import asyncio
import json

import openeyes.mcp.server as mcp_server
from openeyes.core.schema import BBox, Center, Element


def _payload(result):
    return json.loads(result[0].text)


def _call(name, arguments):
    return _payload(asyncio.run(mcp_server.call_tool(name, arguments)))


def _mk(name: str, aid: str = "", ct: str = "Button") -> Element:
    return Element(
        backend="uia", control_type=ct, name=name,
        bbox=BBox(0, 0, 100, 30), center=Center(50, 15), automation_id=aid,
    )


def _stub_elements(monkeypatch, elements):
    monkeypatch.setattr(mcp_server, "detect_elements",
                        lambda *a, **k: list(elements))


# ------------------------------------------------------------- click_by_id
def test_click_by_id_resolves_the_control_by_its_stable_id(monkeypatch):
    """The visible label is a live countdown; the id is the anchor."""
    _stub_elements(monkeypatch, [
        _mk("0:18 left - click to reset", "KeepFreeAliveButton"),
        _mk("Secure my connection", "ConnectButton"),
    ])
    monkeypatch.setattr(
        "openeyes.core.selector.click_element",
        lambda hwnd, **kwargs: {
            "ok": True, "method": "dry-run",
            "name": "0:18 left - click to reset",
            "automation_id": kwargs.get("automation_id", ""),
            "control_type": "Button", "center": (50, 15),
            "landed": None, "error": None,
        },
    )

    result = _call("click_by_id", {
        "hwnd": 42, "automation_id": "KeepFreeAliveButton", "dry_run": True,
    })

    assert result["ok"] is True
    assert result["automation_id"] == "KeepFreeAliveButton"


def test_click_by_id_reports_a_missing_control(monkeypatch):
    _stub_elements(monkeypatch, [_mk("Secure my connection", "ConnectButton")])
    monkeypatch.setattr(
        "openeyes.core.selector.click_element",
        lambda hwnd, **kwargs: {
            "ok": False, "method": None, "name": "",
            "automation_id": kwargs.get("automation_id", ""),
            "control_type": "", "center": None, "landed": None,
            "error": "resolve failed: no such control",
        },
    )

    result = _call("click_by_id", {
        "hwnd": 42, "automation_id": "KeepFreeAliveButton", "dry_run": True,
    })

    assert result["ok"] is False
    assert result["error"]


# --------------------------------------------------------- act_and_verify
def test_act_and_verify_reports_a_miss_when_nothing_changed(monkeypatch):
    """A delivered click that changed nothing is a miss, never a success."""
    _stub_elements(monkeypatch, [
        _mk("0:18 left - click to reset", "KeepFreeAliveButton"),
    ])
    clicked = []

    def fake_click(hwnd, **kwargs):
        clicked.append(kwargs)
        return {"ok": True, "method": "invoke", "name": "0:18 left",
                "automation_id": "KeepFreeAliveButton", "control_type": "Button",
                "center": (50, 15), "landed": None, "error": None}

    monkeypatch.setattr("openeyes.core.selector.click_element", fake_click)

    result = _call("act_and_verify", {
        "hwnd": 42,
        "automation_id": "KeepFreeAliveButton",
        "expect": {"element_appears": {"automation_id": "ConnectButton"}},
        "retries": 2,
        "timeout": 0.1,
        "interval": 0.01,
        "dry_run": False,
    })

    assert result["ok"] is False
    assert result["attempts"] == 2
    assert len(clicked) == 2


def test_act_and_verify_succeeds_when_the_state_is_observed(monkeypatch):
    state = {"appear": False}

    def fake_detect(*_a, **_k):
        elements = [_mk("0:18 left - click to reset", "KeepFreeAliveButton")]
        if state["appear"]:
            elements.append(_mk("Secure my connection", "ConnectButton"))
        return elements

    monkeypatch.setattr(mcp_server, "detect_elements", fake_detect)
    monkeypatch.setattr(
        "openeyes.core.selector.click_element",
        lambda hwnd, **kwargs: {
            "ok": True, "method": "invoke", "name": "0:18 left",
            "automation_id": "KeepFreeAliveButton", "control_type": "Button",
            "center": (50, 15), "landed": None, "error": None,
        },
    )

    # the click "takes effect" only after the first attempt
    calls = {"n": 0}

    def click(hwnd, **kwargs):
        calls["n"] += 1
        if calls["n"] >= 2:
            state["appear"] = True
        return {"ok": True, "method": "invoke", "name": "0:18 left",
                "automation_id": "KeepFreeAliveButton", "control_type": "Button",
                "center": (50, 15), "landed": None, "error": None}

    monkeypatch.setattr("openeyes.core.selector.click_element", click)

    result = _call("act_and_verify", {
        "hwnd": 42,
        "automation_id": "KeepFreeAliveButton",
        "expect": {"element_appears": {"automation_id": "ConnectButton"}},
        "retries": 3,
        "timeout": 0.5,
        "interval": 0.01,
        "dry_run": False,
    })

    assert result["ok"] is True
    assert result["attempts"] >= 1


def test_act_and_verify_dry_run_never_clicks(monkeypatch):
    def fail(*_a, **_k):
        raise AssertionError("dry-run must not actuate")

    monkeypatch.setattr("openeyes.core.selector.click_element", fail)
    _stub_elements(monkeypatch, [
        _mk("0:18 left - click to reset", "KeepFreeAliveButton"),
    ])

    result = _call("act_and_verify", {
        "hwnd": 42,
        "automation_id": "KeepFreeAliveButton",
        "expect": {"element_appears": {"automation_id": "ConnectButton"}},
        "dry_run": True,
    })

    assert result["dry_run"] is True
    assert result["ok"] is False
    assert result["resolved"] is True


def test_act_and_verify_supports_a_vanishing_element(monkeypatch):
    _stub_elements(monkeypatch, [_mk("Secure my connection", "ConnectButton")])
    monkeypatch.setattr(
        "openeyes.core.selector.click_element",
        lambda hwnd, **kwargs: {
            "ok": True, "method": "invoke", "name": "Secure my connection",
            "automation_id": "ConnectButton", "control_type": "Button",
            "center": (50, 15), "landed": None, "error": None,
        },
    )

    result = _call("act_and_verify", {
        "hwnd": 42,
        "automation_id": "ConnectButton",
        "expect": {"element_vanishes": {"automation_id": "ConnectButton"}},
        "retries": 1,
        "timeout": 0.1,
        "interval": 0.01,
        "dry_run": False,
    })

    assert result["ok"] is False
    assert result["observed"]["detail"] == "element is still there"


def test_act_and_verify_supports_a_label_expectation(monkeypatch):
    _stub_elements(monkeypatch, [
        _mk("0:19 left - click to reset", "KeepFreeAliveButton"),
    ])
    monkeypatch.setattr(
        "openeyes.core.selector.click_element",
        lambda hwnd, **kwargs: {
            "ok": True, "method": "invoke", "name": "0:19 left",
            "automation_id": "KeepFreeAliveButton", "control_type": "Button",
            "center": (50, 15), "landed": None, "error": None,
        },
    )

    result = _call("act_and_verify", {
        "hwnd": 42,
        "automation_id": "KeepFreeAliveButton",
        "expect": {"label_matches": {
            "automation_id": "KeepFreeAliveButton",
            "regex": r"0:1\d\s*left",
        }},
        "retries": 1,
        "timeout": 0.1,
        "interval": 0.01,
        "dry_run": False,
    })

    assert result["ok"] is True
    assert result["observed"]["detail"] == "label matched"


def test_act_and_verify_rejects_an_empty_expectation(monkeypatch):
    _stub_elements(monkeypatch, [])
    monkeypatch.setattr(
        "openeyes.core.selector.click_element",
        lambda hwnd, **kwargs: {
            "ok": True, "method": "invoke", "name": "x",
            "automation_id": "", "control_type": "Button",
            "center": (50, 15), "landed": None, "error": None,
        },
    )

    result = _call("act_and_verify", {
        "hwnd": 42,
        "automation_id": "Whatever",
        "expect": {},
        "retries": 1,
        "timeout": 0.1,
        "interval": 0.01,
        "dry_run": False,
    })

    assert result["ok"] is False
    assert "element_appears" in result["observed"]["detail"]

# ------------------------------------------------- fail closed, never guess
def test_click_without_a_selector_is_refused(monkeypatch):
    """An empty call used to click the first element in the window."""
    _stub_elements(monkeypatch, [_mk("Submit")])
    monkeypatch.setattr(
        "openeyes.mcp.server.click_xy",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("clicked")),
    )

    result = _call("click", {"hwnd": 42, "dry_run": False})

    assert "error" in result
    assert "selector" in result["error"]


class _FakeBrowser:
    def __init__(self, elements):
        self.elements = elements
        self.clicked = []

    def connect(self, **kwargs):
        return object()

    def scan_dom(self, conn):
        return list(self.elements)

    def click_center(self, conn, element):
        self.clicked.append(element)


def test_browser_click_with_a_stale_hint_refuses_to_click_the_first_element(
        monkeypatch):
    """A typo in the hint must not turn into a click on elems[0]."""
    fake = _FakeBrowser([_mk("Submit")])
    monkeypatch.setattr(mcp_server, "_browser_backend", lambda args: fake)

    result = _call("browser_click", {"hint": "zz", "go": True})

    assert "error" in result
    assert fake.clicked == []


# ------------------------------------------------- the ladder and its budget
def test_click_selector_uses_the_ladder_and_never_aims_the_pointer(monkeypatch):
    """A selector click asks the control to act; the mouse stays put."""
    _stub_elements(monkeypatch, [_mk("Submit")])
    monkeypatch.setattr(
        "openeyes.mcp.server.click_xy",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("aimed the user's pointer")),
    )
    seen = {}

    def fake_click(hwnd, **kwargs):
        seen.update(kwargs)
        return {"ok": True, "method": "invoke", "name": "Submit",
                "automation_id": "", "control_type": "Button",
                "center": (50, 15), "physical_input": False,
                "focus_stolen": False, "error": None, "error_code": None}

    monkeypatch.setattr("openeyes.core.selector.click_element", fake_click)

    result = _call("click", {"hwnd": 42, "name_contains": "Submit"})

    assert result["method"] == "invoke"
    assert seen["method"] == "auto"
    # the default budget is zero: the pixel rung is not paid for
    assert seen["budget"].per_minute == 0
    assert seen["budget"].allows() is False


def test_click_reports_the_budget_refusal_instead_of_clicking(monkeypatch):
    _stub_elements(monkeypatch, [_mk("Spin", "Spin")])
    monkeypatch.setattr("openeyes.core.selector.click_element", lambda hwnd, **kw: {
        "ok": False, "method": None, "name": "Spin", "automation_id": "Spin",
        "control_type": "Button", "center": (50, 15), "physical_input": False,
        "focus_stolen": False,
        "error": "the disturbance budget does not allow a pixel click",
        "error_code": "BACKGROUND_UNAVAILABLE",
    })

    result = _call("click", {
        "hwnd": 42, "name_contains": "Spin", "dry_run": False,
    })

    assert result["clicked"] is False
    assert result["error_code"] == "BACKGROUND_UNAVAILABLE"


def _retry_probe(monkeypatch):
    """Drive act_and_verify through one retry and record the methods used."""
    _stub_elements(monkeypatch, [_mk("Keep", "Keep")])
    methods = []

    def fake_click(hwnd, **kwargs):
        methods.append(kwargs["method"])
        return {"ok": False, "method": kwargs["method"], "name": "Keep",
                "automation_id": "Keep", "control_type": "Button",
                "center": (50, 15), "landed": None, "error": "not delivered"}

    monkeypatch.setattr("openeyes.core.selector.click_element", fake_click)

    def fake_act_until(act, observe, **kwargs):
        act()
        kwargs["on_retry"](1, None)
        act()
        return {"ok": False, "attempts": 2, "result": None, "elapsed": 0.1,
                "error": "miss"}

    monkeypatch.setattr("openeyes.core.verify.act_until", fake_act_until)
    return methods


def test_act_and_verify_retries_stay_semantic_without_a_budget(monkeypatch):
    """budget=0: a retry alternates semantic rungs, never the pointer."""
    methods = _retry_probe(monkeypatch)

    _call("act_and_verify", {
        "hwnd": 42,
        "automation_id": "Keep",
        "expect": {"element_appears": {"automation_id": "ConnectButton"}},
        "dry_run": False,
    })

    assert "pixel" not in methods
    assert methods == ["auto", "message"]


def test_act_and_verify_retries_may_reach_pixel_when_budget_allows(monkeypatch):
    methods = _retry_probe(monkeypatch)

    _call("act_and_verify", {
        "hwnd": 42,
        "automation_id": "Keep",
        "expect": {"element_appears": {"automation_id": "ConnectButton"}},
        "budget": 1,
        "dry_run": False,
    })

    assert methods == ["auto", "pixel"]