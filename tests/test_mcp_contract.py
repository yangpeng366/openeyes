import asyncio
import json
from unittest.mock import MagicMock

import openeyes.mcp.server as mcp_server
from openeyes.backends.cdp import CDPError
from openeyes.core.schema import BBox, Center, Element


def _payload(result):
    return json.loads(result[0].text)


def _call(name, arguments):
    return _payload(asyncio.run(mcp_server.call_tool(name, arguments)))


def test_mcp_exposes_stable_tool_set():
    tools = asyncio.run(mcp_server.list_tools())
    assert [tool.name for tool in tools] == [
        "list_windows",
        "capture_window",
        "detect_elements",
        "click",
        "grid",
        "hotkey",
        "type_text",
        "browser_launch",
        "browser_tabs",
        "browser_scan",
        "browser_click",
        "browser_type",
        "browser_shot",
        "resolve_elements",
        "vision_detect",
    ]


def test_side_effecting_tools_declare_dry_run_default():
    tools = asyncio.run(mcp_server.list_tools())
    by_name = {tool.name: tool for tool in tools}
    for name in (
        "capture_window",
        "hotkey",
        "type_text",
        "browser_launch",
        "browser_type",
        "browser_shot",
    ):
        assert by_name[name].inputSchema["properties"]["dry_run"] == {
            "type": "boolean",
            "default": True,
        }


def test_native_dry_run_does_not_actuate_or_write(monkeypatch, tmp_path):
    def fail(*args, **kwargs):
        raise AssertionError("side effect called during dry-run")

    monkeypatch.setattr(mcp_server, "capture_screen", fail)
    monkeypatch.setattr(mcp_server, "send_hotkey", fail)
    monkeypatch.setattr(mcp_server, "type_text", fail)

    capture = _call("capture_window", {"out": str(tmp_path / "shot.png")})
    hotkey = _call("hotkey", {"combo": "ctrl+a"})
    typed = _call("type_text", {"text": "hello"})

    assert capture == {
        "captured": False,
        "dry_run": True,
        "path": str(tmp_path / "shot.png"),
        "window": 0,
    }
    assert hotkey["sent"] is False
    assert hotkey["keys"] == ["ctrl", "a"]
    assert typed["sent"] is False
    assert typed["sent_chars"] == 0
    assert typed["would_send_chars"] == 5
    assert not (tmp_path / "shot.png").exists()


def test_browser_dry_run_does_not_launch_or_capture(monkeypatch, tmp_path):
    def fail(*args, **kwargs):
        raise AssertionError("browser side effect called during dry-run")

    monkeypatch.setattr(mcp_server.browser_backend, "launch_edge", fail)
    monkeypatch.setattr(mcp_server.browser_backend, "connect", fail)

    launch = _call("browser_launch", {"url": "https://example.com"})
    shot = _call("browser_shot", {"out": str(tmp_path / "browser.png")})
    typed = _call("browser_type", {"text": "hello", "press_enter": True})

    assert launch["launched"] is False
    assert launch["dry_run"] is True
    assert launch["url"] == "https://example.com"
    assert shot == {
        "captured": False,
        "dry_run": True,
        "path": str(tmp_path / "browser.png"),
    }
    assert typed["sent"] is False
    assert typed["would_send_chars"] == 5
    assert typed["press_enter"] is True
    assert not (tmp_path / "browser.png").exists()


def test_browser_click_forwards_url_filter(monkeypatch):
    seen = {}

    def connect(*, port, url_contains=None):
        seen["port"] = port
        seen["url_contains"] = url_contains
        return object()

    def scan(_conn):
        return [Element(
            backend="cdp",
            control_type="Button",
            name="Continue",
            bbox=BBox(10, 20, 80, 24),
            center=Center(50, 32),
        )]

    monkeypatch.setattr(mcp_server.browser_backend, "connect", connect)
    monkeypatch.setattr(mcp_server.browser_backend, "scan_dom", scan)

    result = _call("browser_click", {
        "name_contains": "Continue",
        "url_contains": "patrol-target",
    })

    assert seen == {"port": 9222, "url_contains": "patrol-target"}
    assert result["target"]["name"] == "Continue"


def test_browser_click_go_false_resolves_without_clicking(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("browser_click actuated despite go=false")

    def scan(_conn):
        return [Element(
            backend="cdp",
            control_type="Button",
            name="Continue",
            bbox=BBox(10, 20, 80, 24),
            center=Center(50, 32),
        )]

    monkeypatch.setattr(mcp_server.browser_backend, "connect", MagicMock)
    monkeypatch.setattr(mcp_server.browser_backend, "scan_dom", scan)
    monkeypatch.setattr(mcp_server.browser_backend, "click_center", fail)

    for arguments in ({"hint": "a"}, {"hint": "a", "go": False}):
        result = _call("browser_click", arguments)

        assert result == {
            "clicked": False,
            "would_click": True,
            "center": {"x": 50, "y": 32},
            "target": {
                "backend": "cdp",
                "control_type": "Button",
                "name": "Continue",
                "bbox": {"x": 10, "y": 20, "w": 80, "h": 24},
                "center": {"x": 50, "y": 32},
                "automation_id": "",
                "class_name": "",
                "score": 1.0,
                "hint": "a",
                "interactive": True,
                "state": {
                    "enabled": True,
                    "visible": True,
                    "focused": False,
                    "selected": False,
                },
                "parent_chain": [],
            },
        }

def test_browser_click_unmatched_url_filter_fails_before_scan_or_click(monkeypatch):
    def connect(*, port, url_contains=None):
        raise CDPError(
            "no page target matched url_contains='missing-tab'; "
            "available URLs: 'https://tab-a', 'https://tab-b'"
        )

    def fail(*args, **kwargs):
        raise AssertionError("browser_click touched a page after url_contains mismatch")
    
    monkeypatch.setattr(mcp_server.browser_backend, "connect", connect)
    monkeypatch.setattr(mcp_server.browser_backend, "scan_dom", fail)
    monkeypatch.setattr(mcp_server.browser_backend, "click_center", fail)
    
    result = _call("browser_click", {
        "name_contains": "Continue",
        "url_contains": "missing-tab",
        "go": True,
    })
    
    assert "no page target matched url_contains='missing-tab'" in result["error"]
    assert result["tool"] == "browser_click"


def test_browser_type_forwards_url_filter(monkeypatch):
    seen = {}
    conn = MagicMock()
    conn.current_url.return_value = "https://example.com/target-a"
    conn.evaluate = MagicMock(return_value=True)

    def connect(*, port, url_contains=None):
        seen["port"] = port
        seen["url_contains"] = url_contains
        return conn

    def scan(_conn):
        return [Element(
            backend="cdp",
            control_type="Edit",
            name="Search",
            bbox=BBox(10, 20, 80, 24),
            center=Center(50, 32),
        )]

    monkeypatch.setattr(mcp_server.browser_backend, "connect", connect)
    monkeypatch.setattr(mcp_server.browser_backend, "scan_dom", scan)
    monkeypatch.setattr(mcp_server.browser_backend, "type_text", lambda *a, **k: None)

    result = _call("browser_type", {
        "text": "hello",
        "name_contains": "Search",
        "url_contains": "patrol-target",
        "dry_run": False,
    })

    assert seen == {"port": 9222, "url_contains": "patrol-target"}
    assert result["sent"] is True
    assert result["sent_chars"] == len("hello")
    assert result["into"] == "Search"
    assert result["target"]["name"] == "Search"
    assert result["target_url"] == "https://example.com/target-a"


def test_browser_type_unmatched_url_filter_fails_before_type(monkeypatch):
    def connect(*, port, url_contains=None):
        raise CDPError(
            "no page target matched url_contains='missing-tab'; "
            "available URLs: 'https://tab-a', 'https://tab-b'"
        )

    def fail(*args, **kwargs):
        raise AssertionError("browser_type touched a page after url_contains mismatch")

    monkeypatch.setattr(mcp_server.browser_backend, "connect", connect)
    monkeypatch.setattr(mcp_server.browser_backend, "scan_dom", fail)
    monkeypatch.setattr(mcp_server.browser_backend, "type_text", fail)

    result = _call("browser_type", {
        "text": "hello",
        "name_contains": "Search",
        "url_contains": "missing-tab",
        "dry_run": False,
    })

    assert "no page target matched url_contains='missing-tab'" in result["error"]
    assert result["tool"] == "browser_type"


def test_browser_type_dry_run_resolves_url_filter_without_selector(monkeypatch):
    seen = {}
    conn = MagicMock()
    conn.current_url.return_value = "https://example.com/target-a"

    def connect(*, port, url_contains=None):
        seen["port"] = port
        seen["url_contains"] = url_contains
        return conn

    def fail(*args, **kwargs):
        raise AssertionError("browser_type dry-run touched a page element")

    monkeypatch.setattr(mcp_server.browser_backend, "connect", connect)
    monkeypatch.setattr(mcp_server.browser_backend, "scan_dom", fail)
    monkeypatch.setattr(mcp_server.browser_backend, "type_text", fail)

    result = _call("browser_type", {
        "text": "hello",
        "url_contains": "target-a",
        "dry_run": True,
    })

    assert seen == {"port": 9222, "url_contains": "target-a"}
    assert result["sent"] is False
    assert result["sent_chars"] == 0
    assert result["would_send_chars"] == 5
    assert result["into"] == "(focused)"
    assert result["target_url"] == "https://example.com/target-a"


def test_browser_type_dry_run_unmatched_url_filter_fails_closed(monkeypatch):
    def connect(*, port, url_contains=None):
        raise CDPError(
            "no page target matched url_contains='missing-tab'; "
            "available URLs: 'https://tab-a', 'https://tab-b'"
        )

    def fail(*args, **kwargs):
        raise AssertionError("browser_type dry-run touched a mismatched page")

    monkeypatch.setattr(mcp_server.browser_backend, "connect", connect)
    monkeypatch.setattr(mcp_server.browser_backend, "scan_dom", fail)
    monkeypatch.setattr(mcp_server.browser_backend, "type_text", fail)

    result = _call("browser_type", {
        "text": "hello",
        "url_contains": "missing-tab",
        "dry_run": True,
    })

    assert "no page target matched url_contains='missing-tab'" in result["error"]
    assert result["tool"] == "browser_type"


def test_browser_shot_without_url_filter_keeps_dry_run_payload(monkeypatch, tmp_path):
    def fail(*args, **kwargs):
        raise AssertionError("filtered dry-run should not be required")

    monkeypatch.setattr(mcp_server.browser_backend, "connect", fail)
    monkeypatch.setattr(mcp_server.browser_backend, "screenshot", fail)

    result = _call("browser_shot", {"out": str(tmp_path / "shot.png")})

    assert result == {
        "captured": False,
        "dry_run": True,
        "path": str(tmp_path / "shot.png"),
    }
    assert not (tmp_path / "shot.png").exists()


def test_browser_shot_url_filter_resolves_target_without_capture(monkeypatch, tmp_path):
    seen = {}
    conn = MagicMock()
    conn.current_url.return_value = "https://example.com/target-a"

    def connect(*, port, url_contains=None):
        seen["port"] = port
        seen["url_contains"] = url_contains
        return conn

    def screenshot(*args, **kwargs):
        raise AssertionError("browser_shot captured during dry-run")

    monkeypatch.setattr(mcp_server.browser_backend, "connect", connect)
    monkeypatch.setattr(mcp_server.browser_backend, "screenshot", screenshot)

    result = _call("browser_shot", {
        "out": str(tmp_path / "shot.png"),
        "url_contains": "target-a",
    })

    assert seen == {"port": 9222, "url_contains": "target-a"}
    assert result["captured"] is False
    assert result["dry_run"] is True
    assert result["target_url"] == "https://example.com/target-a"
    assert not (tmp_path / "shot.png").exists()


def test_browser_shot_unmatched_url_filter_fails_before_capture(monkeypatch, tmp_path):
    def connect(*, port, url_contains=None):
        raise CDPError(
            "no page target matched url_contains='missing-tab'; "
            "available URLs: 'https://tab-a', 'https://tab-b'"
        )

    def fail(*args, **kwargs):
        raise AssertionError("browser_shot captured after url_contains mismatch")

    monkeypatch.setattr(mcp_server.browser_backend, "connect", connect)
    monkeypatch.setattr(mcp_server.browser_backend, "screenshot", fail)

    result = _call("browser_shot", {
        "out": str(tmp_path / "shot.png"),
        "url_contains": "missing-tab",
    })

    assert "no page target matched url_contains='missing-tab'" in result["error"]
    assert result["tool"] == "browser_shot"
    assert not (tmp_path / "shot.png").exists()


def test_browser_type_schema_advertises_url_contains():
    tools = asyncio.run(mcp_server.list_tools())
    by_name = {tool.name: tool for tool in tools}
    schema = by_name["browser_type"].inputSchema
    assert "url_contains" in schema["properties"]
    assert schema["properties"]["url_contains"] == {"type": "string"}

def test_browser_type_rejects_non_editable_selected_target(monkeypatch):
    conn = MagicMock()
    conn.current_url.return_value = "https://example.com/target-a"

    def connect(*, port, url_contains=None):
        return conn

    def scan(_conn):
        return [Element(
            backend="cdp", control_type="Button", name="Search",
            bbox=BBox(10, 20, 80, 24), center=Center(50, 32),
        )]

    def fail(*args, **kwargs):
        raise AssertionError("non-editable target reached type_text")

    monkeypatch.setattr(mcp_server.browser_backend, "connect", connect)
    monkeypatch.setattr(mcp_server.browser_backend, "scan_dom", scan)
    monkeypatch.setattr(mcp_server.browser_backend, "type_text", fail)
    result = _call("browser_type", {
        "text": "hello", "name_contains": "Search", "dry_run": False,
    })
    assert "not editable" in result["error"]
    assert result["tool"] == "browser_type"


def test_browser_type_fails_closed_without_focused_editable_target(monkeypatch):
    conn = MagicMock()

    def connect(*, port, url_contains=None):
        return conn

    def scan(_conn):
        return [Element(
            backend="cdp", control_type="Button", name="Not focused",
            bbox=BBox(10, 20, 80, 24), center=Center(50, 32),
        )]

    def fail(*args, **kwargs):
        raise AssertionError("unverified focused field reached type_text")

    monkeypatch.setattr(mcp_server.browser_backend, "connect", connect)
    monkeypatch.setattr(mcp_server.browser_backend, "scan_dom", scan)
    monkeypatch.setattr(mcp_server.browser_backend, "type_text", fail)
    result = _call("browser_type", {"text": "hello", "dry_run": False})
    assert "no editable" in result["error"]
    assert result["tool"] == "browser_type"


def test_browser_type_rejects_text_not_in_user_source(monkeypatch):
    conn = MagicMock()

    def connect(*, port, url_contains=None):
        return conn

    def scan(_conn):
        return [Element(
            backend="cdp", control_type="Edit", name="Search",
            bbox=BBox(10, 20, 80, 24), center=Center(50, 32),
        )]

    def fail(*args, **kwargs):
        raise AssertionError("unauthorized text reached type_text")

    monkeypatch.setattr(mcp_server.browser_backend, "connect", connect)
    monkeypatch.setattr(mcp_server.browser_backend, "scan_dom", scan)
    monkeypatch.setattr(mcp_server.browser_backend, "type_text", fail)
    result = _call("browser_type", {
        "text": "hello; drop table",
        "source_text": "say hello",
        "name_contains": "Search",
        "dry_run": False,
    })
    assert "not authorized" in result["error"]
    assert result["tool"] == "browser_type"


def test_browser_type_schema_advertises_source_text():
    tools = asyncio.run(mcp_server.list_tools())
    schema = {tool.name: tool for tool in tools}["browser_type"].inputSchema
    assert schema["properties"]["source_text"] == {
        "type": "string",
        "description": "user text that authorizes text",
    }

def test_click_focus_guard_aborts_when_hwnd_is_not_foreground(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("click reached desktop after focus guard failed")

    monkeypatch.setattr(mcp_server, "is_foreground_window", lambda hwnd: False)
    monkeypatch.setattr(mcp_server, "click_xy", fail)
    result = _call("click", {"hwnd": 123, "x": 10, "y": 20, "dry_run": False})
    assert result["error"] == "focus guard: target window is not foreground"
    assert result["hwnd"] == 123
    assert result["tool"] == "click"


def test_click_focus_guard_allows_foreground_hwnd(monkeypatch):
    clicked = {}
    monkeypatch.setattr(mcp_server, "is_foreground_window", lambda hwnd: hwnd == 123)
    def click_xy(x, y, **kwargs):
        clicked["xy"] = (x, y)
        clicked.update(kwargs)
    monkeypatch.setattr(mcp_server, "click_xy", click_xy)
    result = _call("click", {"hwnd": 123, "x": 10, "y": 20, "dry_run": False})
    assert result["clicked"] is True
    assert clicked["xy"] == (10, 20)


def test_vision_detect_parses_a_detections_file(tmp_path):
    path = tmp_path / "det.json"
    path.write_text(json.dumps([[[10, 20, 110, 60], "button"]]), encoding="utf-8")

    result = _call("vision_detect", {"detections_path": str(path)})

    assert result[0]["backend"] == "vision"
    assert result[0]["control_type"] == "Button"
    assert result[0]["center"] == {"x": 60, "y": 40}


def test_vision_detect_accepts_inline_detections():
    result = _call("vision_detect", {
        "detections": json.dumps([[[0, 0, 10, 10], "link"]]),
    })
    assert result[0]["control_type"] == "Hyperlink"


def test_vision_detect_applies_the_origin_offset():
    result = _call("vision_detect", {
        "detections": json.dumps([[[10, 20, 110, 60], "button"]]),
        "origin_x": 100,
        "origin_y": 50,
    })
    assert result[0]["bbox"] == {"x": 110, "y": 70, "w": 100, "h": 40}


def test_vision_detect_reports_a_missing_source():
    result = _call("vision_detect", {})
    assert "detections_path" in result["error"]


def test_vision_detect_reports_a_missing_file(tmp_path):
    result = _call("vision_detect", {"detections_path": str(tmp_path / "nope.json")})
    assert "cannot read" in result["error"]


def test_vision_detect_reports_invalid_json(tmp_path):
    path = tmp_path / "det.json"
    path.write_text("{not json", encoding="utf-8")
    result = _call("vision_detect", {"detections_path": str(path)})
    assert "invalid" in result["error"]


def test_resolve_elements_reports_the_tier_and_the_trail(tmp_path):
    path = tmp_path / "det.json"
    path.write_text(json.dumps([[[10, 20, 110, 60], "button"]]), encoding="utf-8")

    result = _call("resolve_elements", {
        "hwnd": 0, "port": 0, "detections_path": str(path),
    })

    assert result["used_backend"] == "vision"
    assert [a["backend"] for a in result["attempts"]] == ["uia", "cdp", "vision"]
    assert result["attempts"][0]["ok"] is False
    assert result["elements"][0]["backend"] == "vision"


def test_resolve_elements_uses_uia_when_the_window_answers(monkeypatch):
    monkeypatch.setattr(
        "openeyes.core.selector.detect_elements",
        lambda hwnd, restore=False, max_depth=12: [Element(
            backend="uia", control_type="Button", name="Continue",
            bbox=BBox(10, 20, 80, 24), center=Center(50, 32),
        )],
    )
    result = _call("resolve_elements", {"hwnd": 123, "port": 0})
    assert result["used_backend"] == "uia"
    assert result["elements"][0]["name"] == "Continue"


def test_resolve_elements_never_actuates(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("resolve_elements actuated")

    monkeypatch.setattr(mcp_server, "click_xy", fail)
    monkeypatch.setattr(mcp_server, "type_text", fail)
    monkeypatch.setattr(mcp_server, "send_hotkey", fail)
    result = _call("resolve_elements", {"hwnd": 0, "port": 0})
    assert result["used_backend"] is None