"""Behavioural contract tests for examples/dsh-fetch-stall-probe.py.

Each test drives a real code path and asserts a real value: the process exit
code, the JSON evidence written by ``--out``, the exact CDP frames the probe
sends, or the millisecond threshold it computes. Text-only checks (``"2500" in
expr``, ``result["elapsed_ms"] >= 0``) stayed green when the probe was replaced
by a placeholder, so this file no longer relies on them.

Exit-code contract under test (probe docstring):

* ``0`` - page-context fetch answered; no stall.
* ``2`` - page-context fetch stalled while the PowerShell comparison fetch
  succeeded (the dsh symptom).
* ``3`` - CDP attach/evaluate failed; the probe cannot decide.
"""

from __future__ import annotations

import ast
import http.server
import json
import threading
import time
import urllib.error
from pathlib import Path

import pytest
import websocket

ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "examples" / "dsh-fetch-stall-probe.py"

TARGET = {
    "id": "TARGET-1",
    "url": "http://127.0.0.1:3080/session?probe=1",
    "title": "dsh",
    "webSocketDebuggerUrl": "ws://127.0.0.1/devtools/page/abc",
}
RESOLVED_WS = "ws://127.0.0.1:9222/devtools/page/abc"
STORAGE_VALUE = {"value": "sess-1", "keys": ["dsh.sessions.current"]}


def _load_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("dsh_fetch_stall_probe", PROBE)
    assert spec is not None and spec.loader is not None, "probe module spec could not be built"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeWS:
    """Replays queued CDP responses and records every frame the probe sends."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.sent: list[dict] = []
        self.timeouts: list[float] = []
        self.closed = False

    def send(self, raw):
        self.sent.append(json.loads(raw))

    def settimeout(self, value):
        self.timeouts.append(value)

    def recv(self):
        if not self.responses:
            raise AssertionError("probe asked for more CDP responses than the scenario queued")
        item = self.responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return json.dumps(item)

    def close(self):
        self.closed = True


def _evaluate_reply(request_id, value):
    return {"id": request_id, "result": {"result": {"type": "object", "value": value}}}


def _healthy_responses(page_fetch=None):
    return [
        {"id": 1, "result": {}},
        {"id": 2, "result": {}},
        _evaluate_reply(3, {"ok": True, "value": 1}),
        _evaluate_reply(4, page_fetch or {"ok": True, "status": 200, "elapsed_ms": 12}),
        _evaluate_reply(5, STORAGE_VALUE),
    ]


def _start_http(status=200, delay=0.0):
    """A real loopback HTTP server; returns (server, port, observed requests)."""
    observed: list[dict] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - stdlib naming
            observed.append({"path": self.path, "host": self.headers.get("Host")})
            if delay:
                time.sleep(delay)
            self.send_response(status)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, format, *args):  # noqa: A002 - stdlib signature
            return

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1], observed


def _run_probe(monkeypatch, *, argv, responses, tabs=None):
    module = _load_module()
    fake = _FakeWS(responses)
    calls: list[dict] = []

    def _connect(url, **kwargs):
        calls.append({"url": url, **kwargs})
        return fake

    listing = [_TARGET()] if tabs is None else tabs
    monkeypatch.setattr(module, "list_tabs", lambda port=9222: listing)
    monkeypatch.setattr(module.websocket, "create_connection", _connect)
    import sys

    monkeypatch.setattr(sys, "argv", ["dsh-fetch-stall-probe.py", *argv])
    exit_code = module.main()
    return module, exit_code, fake, calls


def _TARGET():
    return dict(TARGET)


def _summary(records):
    summaries = [record for record in records if record.get("kind") == "summary"]
    assert len(summaries) == 1, records
    return summaries[0]["payload"]


# ---------------------------------------------------------------- read-only


def test_probe_ast_contains_no_side_effecting_calls():
    """A comment or docstring mentioning click must not satisfy the guard."""
    tree = ast.parse(PROBE.read_text(encoding="utf-8"))
    forbidden_calls = {
        "click_center",
        "click_xy",
        "focus_element",
        "type_text",
        "screenshot",
        "launch_edge",
        "navigate",
    }
    forbidden_strings = {
        "Page.navigate",
        "Input.dispatchKeyEvent",
        "Input.dispatchMouseEvent",
        "Runtime.evaluate.click",
    }
    called = {
        node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
    }
    assert not (called & forbidden_calls), sorted(called & forbidden_calls)
    literals = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert not (literals & forbidden_strings), sorted(literals & forbidden_strings)


def test_storage_expression_is_a_read_only_single_key_probe():
    """Local storage must be inspected, never mutated."""
    module = _load_module()
    expression = module.STORAGE_EXPRESSION
    assert expression.count("localStorage.getItem('dsh.sessions.current')") == 1
    assert "setItem" not in expression
    assert "removeItem" not in expression
    assert "clear(" not in expression


# ------------------------------------------------------- expression payloads


def test_fetch_expression_issues_a_credentialed_get_with_abort_signal():
    """The page fetch must stay the same GET the PowerShell comparison makes."""
    module = _load_module()
    expr = module.FETCH_EXPRESSION_TEMPLATE.format(timeout_ms=2500, fetch_path="/api/host.describe")
    assert "credentials: 'same-origin'" in expr
    assert "method:" not in expr, "a non-GET page fetch would not be comparable to the GET probe"
    assert "signal: ctrl.signal" in expr
    assert "fetch('/api/host.describe'," in expr
    assert "ctrl.abort(new Error('probe-timeout')), 2500);" in expr
    assert expr.count("clearTimeout(timer);") == 2, "both resolve and reject paths must clear the timer"


def test_cli_fetch_timeout_is_clamped_to_500ms(monkeypatch, tmp_path):
    """The abort timer must be args.timeout - 1s, floored at 500 ms."""
    server, port, _ = _start_http()
    try:
        _, code, fake, _ = _run_probe(
            monkeypatch,
            argv=[
                "--url-contains", "127.0.0.1:3080",
                "--origin", f"http://127.0.0.1:{port}",
                "--timeout", "0.4",
                "--out", str(tmp_path / "evidence.json"),
            ],
            responses=_healthy_responses(),
        )
    finally:
        server.shutdown()
        server.server_close()

    assert code == 0
    expression = fake.sent[3]["params"]["expression"]
    assert "ctrl.abort(new Error('probe-timeout')), 500);" in expression


def test_cli_fetch_timeout_tracks_the_configured_timeout(monkeypatch, tmp_path):
    server, port, _ = _start_http()
    try:
        _, code, fake, calls = _run_probe(
            monkeypatch,
            argv=[
                "--url-contains", "127.0.0.1:3080",
                "--origin", f"http://127.0.0.1:{port}",
                "--timeout", "5",
                "--connect-timeout", "3",
                "--fetch-path", "/api/host.describe",
                "--out", str(tmp_path / "evidence.json"),
            ],
            responses=_healthy_responses(),
        )
    finally:
        server.shutdown()
        server.server_close()

    assert code == 0
    assert "ctrl.abort(new Error('probe-timeout')), 4000);" in fake.sent[3]["params"]["expression"]
    assert "fetch('/api/host.describe'," in fake.sent[3]["params"]["expression"]
    assert calls[0]["timeout"] == 3.0
    assert calls[0]["suppress_origin"] is True
    assert 5.0 in fake.timeouts, "the recv timeout must follow --timeout"


# ------------------------------------------------------------- CDP traffic


def test_cdp_frames_carry_the_documented_methods_and_id_order(monkeypatch, tmp_path):
    """Frame ids, methods and awaitPromise are the wire contract with DevTools."""
    server, port, _ = _start_http()
    try:
        module, code, fake, _ = _run_probe(
            monkeypatch,
            argv=[
                "--url-contains", "127.0.0.1:3080",
                "--origin", f"http://127.0.0.1:{port}",
                "--out", str(tmp_path / "evidence.json"),
            ],
            responses=_healthy_responses(),
        )
    finally:
        server.shutdown()
        server.server_close()

    assert code == 0
    assert [frame["id"] for frame in fake.sent] == [1, 2, 3, 4, 5]
    assert [frame["method"] for frame in fake.sent] == [
        "Runtime.enable",
        "Network.enable",
        "Runtime.evaluate",
        "Runtime.evaluate",
        "Runtime.evaluate",
    ]
    for frame in fake.sent:
        assert frame["params"] is not None
    assert fake.sent[0]["params"] == {}
    assert fake.sent[1]["params"] == {}
    assert fake.sent[2]["params"] == {
        "expression": module.EVENT_LOOP_EXPRESSION,
        "returnByValue": True,
        "awaitPromise": True,
    }
    assert fake.sent[4]["params"]["expression"] == module.STORAGE_EXPRESSION
    assert fake.closed is True


def test_select_target_rewrites_ws_port_without_mutating_the_listing(monkeypatch):
    module = _load_module()
    listing = [dict(TARGET)]
    monkeypatch.setattr(module, "list_tabs", lambda port=9222: listing)

    target = module._select_target(9222, "127.0.0.1:3080")

    assert target["webSocketDebuggerUrl"] == RESOLVED_WS
    assert listing[0]["webSocketDebuggerUrl"] == "ws://127.0.0.1/devtools/page/abc"
    assert target is not listing[0]


def test_select_target_requires_a_match(monkeypatch):
    module = _load_module()
    monkeypatch.setattr(module, "list_tabs", lambda port=9222: [dict(TARGET)])

    with pytest.raises(module.CDPError) as excinfo:
        module._select_target(9222, "missing")

    assert "no page target matched url_contains='missing'" in str(excinfo.value)
    assert TARGET["url"] in str(excinfo.value), "the failure must list the URLs that were available"


# ------------------------------------------------------ PowerShell baseline


def test_power_shell_fetch_sends_host_header_and_measures_real_elapsed_time():
    """A stubbed elapsed_ms of 0 would still satisfy `>= 0`; this pins the wait."""
    module = _load_module()
    server, port, observed = _start_http(delay=0.05)
    try:
        result = module._power_shell_fetch(f"http://127.0.0.1:{port}/api/host.describe", timeout=2.0)
    finally:
        server.shutdown()
        server.server_close()

    assert result["ok"] is True
    assert result["status"] == 200
    assert observed == [{"path": "/api/host.describe", "host": "127.0.0.1"}]
    assert result["elapsed_ms"] >= 30, result


def test_power_shell_fetch_reports_http_error_status(monkeypatch):
    """HTTP errors are statuses, not generic failures: 401 from dsh is meaningful."""
    module = _load_module()
    server, port, _ = _start_http(status=401)
    try:
        result = module._power_shell_fetch(f"http://127.0.0.1:{port}/", timeout=2.0)
    finally:
        server.shutdown()
        server.server_close()

    assert result["ok"] is False
    assert result["status"] == 401
    assert "401" in result["error"]
    assert isinstance(result["elapsed_ms"], int)


def test_power_shell_fetch_reports_connection_failure(monkeypatch):
    module = _load_module()

    def _raise(req, timeout=5.0):  # noqa: ARG001
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(module.urllib.request, "urlopen", _raise)
    result = module._power_shell_fetch("http://127.0.0.1:1", timeout=1.0)
    assert result["ok"] is False
    assert result["status"] == 0
    assert "connection refused" in result["error"]


# --------------------------------------------------------------- exit codes


def test_page_stall_exits_2_and_writes_the_evidence_file(monkeypatch, tmp_path, capsys):
    """Regressing fetch_stalled to a constant or the exit code to 0 fails here."""
    server, port, observed = _start_http(delay=0.05)
    out_path = tmp_path / "evidence" / "stall.json"
    try:
        _, code, fake, _ = _run_probe(
            monkeypatch,
            argv=[
                "--url-contains", "127.0.0.1:3080",
                "--origin", f"http://127.0.0.1:{port}",
                "--timeout", "5",
                "--out", str(out_path),
            ],
            responses=[
                {"id": 1, "result": {}},
                {"id": 2, "result": {}},
                _evaluate_reply(3, {"ok": True, "value": 1}),
                websocket.WebSocketTimeoutException("timed out"),
                _evaluate_reply(5, STORAGE_VALUE),
            ],
        )
    finally:
        server.shutdown()
        server.server_close()

    assert code == 2
    assert observed == [{"path": "/", "host": "127.0.0.1"}]

    raw = out_path.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf"), "--out must be UTF-8 without BOM"
    assert raw.endswith(b"\n")
    records = [json.loads(line) for line in raw.decode("utf-8").splitlines()]
    assert [record["kind"] for record in records] == ["attached", "summary"]

    attached = records[0]["payload"]
    assert attached == {
        "url": TARGET["url"],
        "title": TARGET["title"],
        "target_id": TARGET["id"],
        "webSocketDebuggerUrl": RESOLVED_WS,
    }

    summary = _summary(records)
    assert summary["event_loop_ok"] is True
    assert summary["dsh_sessions_current"] == STORAGE_VALUE
    assert summary["fetch_stalled"] is True
    page_fetch = summary["page_fetch"]
    assert page_fetch["ok"] is False
    assert page_fetch["status"] == 0
    assert page_fetch["error"].startswith("page fetch stalled: Runtime.evaluate: receive timeout")
    assert page_fetch["elapsed_ms"] == 5000, "the stalled elapsed time is --timeout, in ms"
    assert summary["ps_fetch"]["ok"] is True
    assert summary["ps_fetch"]["status"] == 200
    assert summary["ps_fetch"]["elapsed_ms"] >= 30

    stdout_lines = [line for line in capsys.readouterr().out.splitlines() if line]
    assert stdout_lines == raw.decode("utf-8").splitlines(), "stdout and --out must carry the same records"


def test_healthy_page_and_host_exit_0_without_stall(monkeypatch, tmp_path):
    server, port, _ = _start_http()
    try:
        _, code, _, _ = _run_probe(
            monkeypatch,
            argv=[
                "--url-contains", "127.0.0.1:3080",
                "--origin", f"http://127.0.0.1:{port}",
                "--out", str(tmp_path / "healthy.json"),
            ],
            responses=_healthy_responses({"ok": True, "status": 200, "elapsed_ms": 12}),
        )
    finally:
        server.shutdown()
        server.server_close()

    records = [
        json.loads(line)
        for line in (tmp_path / "healthy.json").read_text(encoding="utf-8").splitlines()
    ]
    assert code == 0
    assert _summary(records)["fetch_stalled"] is False


def test_page_http_error_is_not_a_stall(monkeypatch, tmp_path):
    """A 500 from the page fetch means the request completed; only status 0 stalls."""
    server, port, _ = _start_http()
    try:
        _, code, _, _ = _run_probe(
            monkeypatch,
            argv=[
                "--url-contains", "127.0.0.1:3080",
                "--origin", f"http://127.0.0.1:{port}",
                "--out", str(tmp_path / "http-error.json"),
            ],
            responses=_healthy_responses(
                {"ok": False, "status": 500, "error": "HTTP 500", "elapsed_ms": 40}
            ),
        )
    finally:
        server.shutdown()
        server.server_close()

    records = [
        json.loads(line)
        for line in (tmp_path / "http-error.json").read_text(encoding="utf-8").splitlines()
    ]
    summary = _summary(records)
    assert code == 0
    assert summary["fetch_stalled"] is False
    assert summary["page_fetch"]["status"] == 500


def test_missing_target_exits_3_and_writes_no_evidence_file(monkeypatch, tmp_path, capsys):
    out_path = tmp_path / "unused.json"
    _, code, _, _ = _run_probe(
        monkeypatch,
        argv=[
            "--url-contains", "no-such-tab",
            "--out", str(out_path),
        ],
        responses=[],
    )

    assert code == 3
    assert not out_path.exists()
    error = json.loads(capsys.readouterr().err.splitlines()[-1])
    assert error["ready"] is False
    assert "no page target matched url_contains='no-such-tab'" in error["error"]


def test_page_expression_exception_exits_3_instead_of_reporting_a_stall(monkeypatch, tmp_path, capsys):
    """A throwing page is an attach failure, not the dsh fetch stall."""
    server, port, _ = _start_http()
    try:
        _, code, _, _ = _run_probe(
            monkeypatch,
            argv=[
                "--url-contains", "127.0.0.1:3080",
                "--origin", f"http://127.0.0.1:{port}",
                "--out", str(tmp_path / "exception.json"),
            ],
            responses=[
                {"id": 1, "result": {}},
                {"id": 2, "result": {}},
                _evaluate_reply(3, {"ok": True, "value": 1}),
                {"id": 4, "result": {"exceptionDetails": {"text": "ReferenceError: fetch is not defined"}}},
                _evaluate_reply(5, STORAGE_VALUE),
            ],
        )
    finally:
        server.shutdown()
        server.server_close()

    assert code == 3
    error = json.loads(capsys.readouterr().err.splitlines()[-1])
    assert "page expression raised" in error["error"]
    assert "fetch is not defined" in error["error"]


# ----------------------------------------------------------------------- CLI


def test_cli_help_lists_every_documented_flag():
    import os
    import subprocess
    import sys

    environment = os.environ.copy()
    # The probe imports the openeyes package; a bare script run puts only
    # examples/ on sys.path.
    environment["PYTHONPATH"] = str(ROOT)
    # argparse prints the module docstring, which contains non-ASCII dashes;
    # pin the child encoding so the help text decodes the same on every locale.
    environment["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run(
        [sys.executable, str(PROBE), "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=60,
        env=environment,
    )
    assert result.returncode == 0
    for flag in ("--port", "--url-contains", "--timeout", "--connect-timeout",
                 "--fetch-path", "--origin", "--out"):
        assert flag in result.stdout, flag
    assert "Exit ``0``" in result.stdout or "Exit 0" in result.stdout, result.stdout
    assert result.stderr == ""
