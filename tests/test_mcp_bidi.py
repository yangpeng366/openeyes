from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import openeyes.mcp.server as mcp_server
from openeyes.core.schema import BBox, Center, Element


def _payload(result):
    return json.loads(result[0].text)


def _call(name, arguments):
    return _payload(asyncio.run(mcp_server.call_tool(name, arguments)))


def _bidi_backend(monkeypatch, *, elements=None):
    conn = MagicMock()
    backend = SimpleNamespace(
        connect=MagicMock(return_value=conn),
        scan_dom=MagicMock(return_value=elements or []),
        list_tabs=MagicMock(return_value=[{
            "id": "tab-1", "title": "飞书", "url": "https://feishu.cn", "type": "page",
        }]),
        click_center=MagicMock(),
        type_text=MagicMock(),
        screenshot=MagicMock(return_value="shot.png"),
    )
    monkeypatch.setattr(mcp_server.browser_api, "backend_for", lambda protocol: backend)
    return backend, conn


def test_browser_schemas_advertise_cdp_and_bidi():
    tools = asyncio.run(mcp_server.list_tools())
    by_name = {tool.name: tool for tool in tools}
    for name in ("browser_tabs", "browser_scan", "browser_click",
                 "browser_type", "browser_shot"):
        schema = by_name[name].inputSchema
        assert schema["properties"]["protocol"] == {
            "type": "string", "enum": ["cdp", "bidi"], "default": "cdp",
        }


def test_browser_tabs_routes_explicit_bidi(monkeypatch):
    backend, _conn = _bidi_backend(monkeypatch)

    result = _call("browser_tabs", {"port": 9222, "protocol": "bidi"})

    assert result == [{
        "id": "tab-1", "title": "飞书", "url": "https://feishu.cn", "type": "page",
    }]
    backend.list_tabs.assert_called_once_with(9222)


def test_browser_scan_routes_bidi_and_closes_the_session(monkeypatch):
    element = Element(
        backend="bidi", control_type="Button", name="验证码登录",
        bbox=BBox(10, 20, 90, 30), center=Center(55, 35),
    )
    backend, conn = _bidi_backend(monkeypatch, elements=[element])

    result = _call("browser_scan", {
        "port": 19222, "protocol": "bidi", "host_header": "localhost:9222",
        "url_contains": "feishu.cn",
    })

    assert result[0]["backend"] == "bidi"
    backend.connect.assert_called_once_with(
        port=19222, url_contains="feishu.cn", host_header="localhost:9222",
    )
    backend.scan_dom.assert_called_once_with(conn)
    conn.close.assert_called_once_with()


def test_browser_click_bidi_keeps_default_dry_run(monkeypatch):
    element = Element(
        backend="bidi", control_type="Button", name="验证码登录",
        bbox=BBox(10, 20, 90, 30), center=Center(55, 35),
    )
    backend, conn = _bidi_backend(monkeypatch, elements=[element])

    result = _call("browser_click", {
        "protocol": "bidi", "name_contains": "验证码",
    })

    assert result["clicked"] is False
    assert result["would_click"] is True
    backend.click_center.assert_not_called()
    conn.close.assert_called_once_with()


def test_browser_type_forwards_bidi_host_header(monkeypatch):
    backend, conn = _bidi_backend(monkeypatch)
    conn.current_url.return_value = "https://accounts.feishu.cn/login"

    result = _call("browser_type", {
        "port": 19222,
        "protocol": "bidi",
        "host_header": "localhost:9222",
        "url_contains": "feishu.cn",
        "text": "15928153396",
        "dry_run": True,
    })

    assert result["dry_run"] is True
    assert result["target_url"] == "https://accounts.feishu.cn/login"
    backend.connect.assert_called_once_with(
        port=19222, url_contains="feishu.cn", host_header="localhost:9222",
    )
    conn.close.assert_called_once_with()


def test_browser_shot_forwards_bidi_host_header(monkeypatch, tmp_path):
    backend, conn = _bidi_backend(monkeypatch)
    conn.current_url.return_value = "https://feishu.cn/messenger"
    out = str(tmp_path / "shot.png")

    result = _call("browser_shot", {
        "port": 19222,
        "protocol": "bidi",
        "host_header": "localhost:9222",
        "url_contains": "feishu.cn",
        "out": out,
        "dry_run": True,
    })

    assert result["dry_run"] is True
    assert result["captured"] is False
    assert result["target_url"] == "https://feishu.cn/messenger"
    backend.connect.assert_called_once_with(
        port=19222, url_contains="feishu.cn", host_header="localhost:9222",
    )
    conn.close.assert_called_once_with()
