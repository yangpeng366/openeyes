"""Contract tests for examples/dsh-fetch-stall-probe.py.

These tests do not require a live browser. They verify that the probe:

- stays read-only (no click / type / navigate / focus calls)
- formats the page-context fetch expression safely
- derives the `fetch_stalled` boolean from page + PowerShell results
"""

from __future__ import annotations

import importlib.util
import urllib.error
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "examples" / "dsh-fetch-stall-probe.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("dsh_fetch_stall_probe", PROBE)
    assert spec is not None and spec.loader is not None, "probe module spec could not be built"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_probe_source_does_not_import_side_effecting_helpers():
    source = PROBE.read_text(encoding="utf-8")
    forbidden = (
        "click_center",
        "click_xy",
        "focus_element",
        "type_text",
        "screenshot",
        "launch_edge",
        "Page.navigate",
        "Input.dispatchKeyEvent",
        "Input.dispatchMouseEvent",
    )
    for token in forbidden:
        assert token not in source, f"probe must not reference side-effecting helper {token!r}"


def test_probe_documents_safety_in_module_docstring():
    source = PROBE.read_text(encoding="utf-8")
    assert "read-only" in source
    assert "does NOT click" in source
    assert "does NOT" in source


def test_fetch_expression_includes_timeout_and_signal():
    module = _load_module()
    expr = module.FETCH_EXPRESSION_TEMPLATE.format(timeout_ms=2500, fetch_path="/api/host.describe")
    assert "AbortController" in expr
    assert "signal:" in expr
    assert "2500" in expr
    assert "/api/host.describe" in expr


def test_storage_expression_filters_session_keys():
    module = _load_module()
    assert "dsh.sessions.current" in module.STORAGE_EXPRESSION
    assert "dsh.sessions" in module.STORAGE_EXPRESSION


def test_power_shell_fetch_returns_structured_dict(monkeypatch):
    module = _load_module()

    class _StubResponse:
        status = 204

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def _stub_urlopen(req, timeout=5.0):  # noqa: ARG001
        return _StubResponse()

    monkeypatch.setattr(module.urllib.request, "urlopen", _stub_urlopen)
    result = module._power_shell_fetch("http://127.0.0.1:3080/api/host.describe", timeout=2.0)
    assert result["ok"] is True
    assert result["status"] == 204
    assert result["elapsed_ms"] >= 0


def test_power_shell_fetch_reports_connection_failure(monkeypatch):
    module = _load_module()

    def _raise(req, timeout=5.0):  # noqa: ARG001
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(module.urllib.request, "urlopen", _raise)
    result = module._power_shell_fetch("http://127.0.0.1:1", timeout=1.0)
    assert result["ok"] is False
    assert result["status"] == 0
    assert "connection refused" in result["error"]


def test_select_target_requires_a_match(monkeypatch):
    module = _load_module()
    monkeypatch.setattr(
        module,
        "list_tabs",
        lambda port=9222: [
            {"url": "http://127.0.0.1:3080/", "webSocketDebuggerUrl": "ws://127.0.0.1/devtools/page/1"},
        ],
    )
    with pytest.raises(module.CDPError):
        module._select_target(9222, "missing")


def test_select_target_rewrites_websocket_url(monkeypatch):
    module = _load_module()
    monkeypatch.setattr(
        module,
        "list_tabs",
        lambda port=9222: [
            {
                "url": "http://127.0.0.1:3080/session",
                "webSocketDebuggerUrl": "ws://127.0.0.1/devtools/page/abc",
            },
        ],
    )
    target = module._select_target(9222, "127.0.0.1:3080")
    assert target["webSocketDebuggerUrl"] == "ws://127.0.0.1:9222/devtools/page/abc"


def test_cli_help_lists_expected_flags():
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, str(PROBE), "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=10,
    )
    assert result.returncode == 0
    assert "--url-contains" in result.stdout
    assert "--timeout" in result.stdout
    assert "--fetch-path" in result.stdout