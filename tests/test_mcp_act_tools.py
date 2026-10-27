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
