"""Tests for the CDP backend. WebSocket calls are mocked; no real browser needed."""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from openeyes.backends import cdp as cdpmod


# ---------------------------------------------------------------------------
# _make_element + scan_dom parsing
# ---------------------------------------------------------------------------

def test_make_element_parses_raw_dict():
    raw = {
        "tag": "button",
        "role": "",
        "type": "submit",
        "name": "Reset",
        "automation_id": "btnReset",
        "class_name": "btn primary",
        "bbox": {"x": 100, "y": 200, "w": 80, "h": 30},
        "center": {"x": 140, "y": 215},
        "parent_chain": ["form", "div"],
        "enabled": True,
        "visible": True,
        "focused": False,
    }
    raw["control_type"] = cdpmod._js_control_type_map(raw)
    e = cdpmod._make_element(raw)
    assert e.backend == "cdp"
    assert e.control_type == "Button"
    assert e.name == "Reset"
    assert e.bbox.w == 80 and e.bbox.h == 30
    assert e.center.x == 140 and e.center.y == 215
    assert e.parent_chain == ["form", "div"]
    assert e.state.enabled and e.state.visible and not e.state.focused


def test_control_type_map_for_common_inputs():
    def case(tag, role="", type_="") -> str:
        return cdpmod._js_control_type_map({"tag": tag, "role": role, "type": type_})
    assert case("a") == "Hyperlink"
    assert case("a", "link") == "Hyperlink"
    assert case("button") == "Button"
    assert case("input", type_="submit") == "Button"
    assert case("input") == "Edit"
    assert case("input", type_="checkbox") == "CheckBox"
    assert case("input", type_="radio") == "RadioButton"
    assert case("textarea") == "Edit"
    assert case("select") == "ComboBox"
    assert case("div", role="checkbox") == "CheckBox"
    assert case("div", role="tab") == "Tab"
    assert case("div", role="menuitem") == "MenuItem"


def test_scan_dom_parses_js_result_into_elements():
    fake = cdpmod.CDPConnection.__new__(cdpmod.CDPConnection)
    fake._ws = MagicMock()
    fake._id = 0

    # The probe script itself filters zero-area + hidden elements before
    # returning; the JS result we mock here is what would survive that step.
    js_result = [
        {"tag": "button", "name": "OK", "role": "", "type": "",
         "bbox": {"x": 10, "y": 10, "w": 80, "h": 24},
         "center": {"x": 50, "y": 22}, "automation_id": "okBtn",
         "class_name": "btn", "parent_chain": [],
         "enabled": True, "visible": True, "focused": False,
         "text": "OK", "selector": "button"},
        {"tag": "a", "name": "More info", "role": "", "type": "",
         "bbox": {"x": 100, "y": 100, "w": 100, "h": 30},
         "center": {"x": 150, "y": 115}, "automation_id": "",
         "class_name": "", "parent_chain": [],
         "enabled": True, "visible": True, "focused": False,
         "text": "More information...", "selector": "a"},
    ]

    def fake_call(method, params=None, timeout=20.0):
        if method == "Runtime.evaluate":
            return {"result": {"value": js_result}}
        raise AssertionError(method)

    fake.call = fake_call
    out = cdpmod.scan_dom(fake, limit=10)
    assert len(out) == 2
    assert out[0].control_type == "Button" and out[0].name == "OK"
    assert out[0].automation_id == "okBtn"
    assert out[1].control_type == "Hyperlink"


def test_scan_dom_returns_empty_on_non_list():
    fake = MagicMock()
    fake.call = MagicMock(return_value={"result": {"value": "not a list"}})
    out = cdpmod.scan_dom(fake)
    assert out == []


# ---------------------------------------------------------------------------
# list_tabs / connect (HTTP + WebSocket discovery)
# ---------------------------------------------------------------------------

def test_list_tabs_filters_service_workers():
    pages = [
        {"type": "page", "id": "1", "url": "https://example.com",
         "title": "Example", "webSocketDebuggerUrl": "ws://1"},
        {"type": "service_worker", "id": "2", "url": "sw.js",
         "title": "", "webSocketDebuggerUrl": "ws://2"},
    ]
    with patch.object(cdpmod, "_http_get_json", return_value=pages) as m:
        got = cdpmod.list_tabs(9222)
    assert m.call_count == 1
    assert [t["id"] for t in got] == ["1"]


def test_connect_picks_first_page_when_no_filter():
    pages = [
        {"type": "page", "id": "1", "url": "https://a",
         "webSocketDebuggerUrl": "ws://x"},
        {"type": "page", "id": "2", "url": "https://b",
         "webSocketDebuggerUrl": "ws://y"},
    ]
    fake = MagicMock()
    fake.recv = MagicMock(return_value=json.dumps({"id": 1, "result": {}}))
    fake.settimeout = MagicMock()
    with patch.object(cdpmod, "_http_get_json", return_value=pages), \
         patch.object(cdpmod.websocket, "create_connection", return_value=fake):
        conn = cdpmod.connect(port=9222)
    assert conn.ws_url == "ws://x"


def test_connect_url_filter_picks_matching_tab():
    pages = [
        {"type": "page", "id": "1", "url": "https://other",
         "webSocketDebuggerUrl": "ws://x"},
        {"type": "page", "id": "2", "url": "https://target",
         "webSocketDebuggerUrl": "ws://y"},
    ]
    fake = MagicMock()
    fake.recv = MagicMock(return_value=json.dumps({"id": 1, "result": {}}))
    fake.settimeout = MagicMock()
    with patch.object(cdpmod, "_http_get_json", return_value=pages), \
         patch.object(cdpmod.websocket, "create_connection", return_value=fake):
        conn = cdpmod.connect(port=9222, url_contains="target")
    assert conn.ws_url == "ws://y"

def test_connect_url_filter_rejects_when_no_tab_matches():
    pages = [
        {"type": "page", "id": "1", "url": "https://other",
         "webSocketDebuggerUrl": "ws://x"},
        {"type": "page", "id": "2", "url": "https://another",
         "webSocketDebuggerUrl": "ws://y"},
    ]
    with patch.object(cdpmod, "_http_get_json", return_value=pages), \
         pytest.raises(cdpmod.CDPError, match="no page target matched url_contains='target'"):
        cdpmod.connect(port=9222, url_contains="target")


# ---------------------------------------------------------------------------
# click_center / type_text / focus_element
# ---------------------------------------------------------------------------

def test_click_center_sends_two_events_at_center():
    fake = MagicMock()
    fake.recv = MagicMock(side_effect=[
        json.dumps({"id": 1, "result": {}}),
        json.dumps({"id": 2, "result": {}}),
    ])
    fake.settimeout = MagicMock()
    from openeyes.core.schema import BBox, Center, Element
    e = Element(backend="cdp", control_type="Button", name="x",
                bbox=BBox(95, 195, 10, 10), center=Center(100, 200))
    conn = cdpmod.CDPConnection.__new__(cdpmod.CDPConnection)
    conn.ws_url = "ws://x"
    conn._id = 0
    conn._ws = fake
    cdpmod.click_center(conn, e)
    assert fake.send.call_count == 2
    payloads = [json.loads(fake.send.call_args_list[0].args[0]),
                json.loads(fake.send.call_args_list[1].args[0])]
    assert payloads[0]["method"] == "Input.dispatchMouseEvent"
    assert payloads[0]["params"]["type"] == "mousePressed"
    assert payloads[0]["params"]["x"] == 100
    assert payloads[1]["params"]["type"] == "mouseReleased"


def test_type_text_uses_insertText_so_unicode_survives():
    conn = MagicMock()
    cdpmod.type_text(conn, "hello 你好")
    methods = [c.args[0] for c in conn.call.call_args_list]
    assert "Input.insertText" in methods
    # The full UTF-8 string was forwarded in the second positional argument.
    texts = [c.args[1].get("text") for c in conn.call.call_args_list]
    assert "hello 你好" in texts


def test_type_text_press_enter_sends_key_down_then_key_up():
    conn = MagicMock()
    cdpmod.type_text(conn, "abc", press_enter=True)
    methods = [c.args[0] for c in conn.call.call_args_list]
    assert methods.count("Input.dispatchKeyEvent") == 2
    assert "Input.insertText" in methods


def test_focus_element_returns_true_when_focused():
    conn = MagicMock()
    conn.evaluate = MagicMock(return_value={"ok": True, "tag": "input"})
    from openeyes.core.schema import BBox, Center, Element
    e = Element(backend="cdp", control_type="Edit", name="q",
                automation_id="q", class_name="",
                bbox=BBox(0, 0, 10, 10), center=Center(5, 5))
    assert cdpmod.focus_element(conn, e) is True


def test_focus_element_returns_false_on_no_match():
    conn = MagicMock()
    conn.evaluate = MagicMock(return_value={"ok": False, "msg": "no"})
    from openeyes.core.schema import BBox, Center, Element
    e = Element(backend="cdp", control_type="Edit", name="q",
                bbox=BBox(0, 0, 10, 10), center=Center(5, 5))
    assert cdpmod.focus_element(conn, e) is False


# ---------------------------------------------------------------------------
# DOM probe payload is well-formed
# ---------------------------------------------------------------------------

def test_dom_js_is_single_function_expression():
    js = cdpmod._DOM_JS.strip()
    assert js.startswith("(function")
    assert "return out" in js
    assert "querySelectorAll" in js


def test_edge_exe_finds_one_of_known_paths(monkeypatch, tmp_path):
    """At least one of the candidate Edge paths should resolve when Edge is
    actually installed. On machines without Edge the helper raises CDPError.
    """
    import os
    paths = list(cdpmod.EDGE_CANDIDATES)
    if not any(os.path.exists(p) for p in paths):
        with pytest.raises(cdpmod.CDPError):
            cdpmod._edge_exe()
    else:
        e = cdpmod._edge_exe()
        assert e.lower().endswith("msedge.exe") or e.lower().endswith("chrome.exe")


def test_edge_exe_honors_environment_override(monkeypatch, tmp_path):
    edge = tmp_path / "custom-edge.exe"
    edge.write_bytes(b"placeholder")
    monkeypatch.setenv("EDGE_EXE", str(edge))

    assert cdpmod._edge_exe() == str(edge)


def test_edge_exe_rejects_missing_environment_override(monkeypatch, tmp_path):
    missing = tmp_path / "missing-edge.exe"
    monkeypatch.setenv("EDGE_EXE", str(missing))

    with pytest.raises(cdpmod.CDPError, match="EDGE_EXE does not point to a file"):
        cdpmod._edge_exe()


# ---------------------------------------------------------------------------
# launch_edge: bounded diagnostic + stale-lock fallback
# ---------------------------------------------------------------------------

class _FakeProc:
    def __init__(self, *, exited: bool, pid: int = 24001):
        self._exited = exited
        self.pid = pid
        self.terminate = MagicMock()
        self.wait = MagicMock()

    def poll(self):
        return 0 if self._exited else None


def _patch_launch(monkeypatch, tmp_path, *, list_tabs_side):
    monkeypatch.setattr(cdpmod, "_edge_exe", lambda: str(tmp_path / "edge.exe"))
    monkeypatch.setattr(cdpmod, "list_tabs", MagicMock(side_effect=list_tabs_side))
    clock = {"v": 0.0}
    monkeypatch.setattr(cdpmod.time, "time", lambda: clock["v"])
    monkeypatch.setattr(cdpmod.time, "sleep", lambda s, *a, **k: clock.__setitem__("v", clock["v"] + s))


def test_launch_edge_diagnostic_when_cdp_never_exposes(monkeypatch, tmp_path):
    _patch_launch(monkeypatch, tmp_path, list_tabs_side=ConnectionError("refused"))
    fake_proc = _FakeProc(exited=True)
    monkeypatch.setattr(cdpmod.subprocess, "Popen", MagicMock(return_value=fake_proc))
    profile = tmp_path / "openeyes-edge"
    with pytest.raises(cdpmod.CDPError) as ei:
        cdpmod.launch_edge(port=9333, profile_dir=profile, seed=False, wait_ms=1, retries=0)
    msg = str(ei.value)
    assert "Edge did not expose CDP" in msg
    assert "port=9333" in msg
    assert "process=exited" in msg
    assert "last_error=" in msg


def test_launch_edge_reports_process_alive_and_terminates(monkeypatch, tmp_path):
    _patch_launch(monkeypatch, tmp_path, list_tabs_side=ConnectionError("refused"))
    fake_proc = _FakeProc(exited=False)
    monkeypatch.setattr(cdpmod.subprocess, "Popen", MagicMock(return_value=fake_proc))
    profile = tmp_path / "openeyes-edge"
    with pytest.raises(cdpmod.CDPError) as ei:
        cdpmod.launch_edge(port=9333, profile_dir=profile, seed=False, wait_ms=1, retries=1)
    assert "process=alive" in str(ei.value)
    fake_proc.terminate.assert_called_once()
    fake_proc.wait.assert_called_once()


def test_launch_edge_retries_after_clearing_stale_singleton_lock(monkeypatch, tmp_path):
    _patch_launch(monkeypatch, tmp_path, list_tabs_side=ConnectionError("refused"))
    fake_proc = _FakeProc(exited=True)
    popen = MagicMock(return_value=fake_proc)
    monkeypatch.setattr(cdpmod.subprocess, "Popen", popen)
    profile = tmp_path / "openeyes-edge"
    profile.mkdir(parents=True, exist_ok=True)
    (profile / "SingletonLock").write_bytes(b"x")
    with pytest.raises(cdpmod.CDPError):
        cdpmod.launch_edge(port=9333, profile_dir=profile, seed=False, wait_ms=1, retries=1)
    # bounded single retry happened after the process exited early
    assert popen.call_count == 2
    # stale lock was cleared before the retry
    assert not (profile / "SingletonLock").exists()


def test_launch_edge_no_retry_when_process_alive(monkeypatch, tmp_path):
    _patch_launch(monkeypatch, tmp_path, list_tabs_side=ConnectionError("refused"))
    fake_proc = _FakeProc(exited=False)
    popen = MagicMock(return_value=fake_proc)
    monkeypatch.setattr(cdpmod.subprocess, "Popen", popen)
    profile = tmp_path / "openeyes-edge"
    profile.mkdir(parents=True, exist_ok=True)
    (profile / "SingletonLock").write_bytes(b"x")
    with pytest.raises(cdpmod.CDPError):
        cdpmod.launch_edge(port=9333, profile_dir=profile, seed=False, wait_ms=1, retries=1)
    # still-alive process: no retry, lock untouched
    assert popen.call_count == 1
    assert (profile / "SingletonLock").exists()


def test_profile_is_disposable_true_under_temp(tmp_path):
    assert cdpmod._profile_is_disposable(tmp_path) is True


def test_profile_is_disposable_false_outside_temp():
    assert cdpmod._profile_is_disposable(cdpmod.Path(r"C:\Program Files\openeyes-fake-profile")) is False


def test_clear_singleton_locks_removes_only_lock_files(tmp_path):
    d = tmp_path / "prof"
    d.mkdir()
    for name in cdpmod._SINGLETON_LOCK_NAMES:
        (d / name).write_bytes(b"x")
    keeper = d / "Preferences"
    keeper.write_bytes(b"keep")
    removed = cdpmod._clear_singleton_locks(d)
    assert set(removed) == set(cdpmod._SINGLETON_LOCK_NAMES)
    for name in cdpmod._SINGLETON_LOCK_NAMES:
        assert not (d / name).exists()
    assert keeper.exists()