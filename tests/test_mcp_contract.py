import asyncio
import json
from unittest.mock import MagicMock

import openeyes.mcp.server as mcp_server
from openeyes.core.schema import BBox, Center, Element

from openeyes.backends.cdp import CDPError

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
    assert result["clicked"] is False
    assert result["would_click"] is True
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
