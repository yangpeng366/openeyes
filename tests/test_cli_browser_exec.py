"""``eyes browser exec`` - the code-first escape hatch.

The fixed verb set can only say what somebody modelled.  ``browser exec``
hands the page's own JavaScript to the caller, so a loop, a condition or a
widget the harness never saw is still expressible.  These tests pin the
parts that must not drift:

* a dry-run reports the payload and does not touch the browser at all;
* ``--go`` runs the code and returns its value;
* the source may come from ``--code`` or ``--file``, never both;
* a page error names its cause instead of failing silently;
* ``--url-contains`` fails closed rather than falling back to another tab.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import types

import pytest
from click.testing import CliRunner

import openeyes.backends.browser as browser_api
from openeyes.cli.main import cli
from openeyes.mcp import server as mcp_server


class _Conn:
    """The little of a CDP connection the command actually touches."""

    def __init__(self, url="https://example.test/page"):
        self.url = url
        self.closed = False

    def current_url(self):
        return self.url

    def close(self):
        self.closed = True


def _stub(connect=None, exec_js=None):
    backend = types.SimpleNamespace(
        connect=connect or (lambda **kwargs: _Conn()),
        exec_js=exec_js or (lambda conn, expression, **kwargs: None),
    )
    return backend


def _install(monkeypatch, backend):
    monkeypatch.setattr(browser_api, "backend_for", lambda protocol: backend)


def _sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def test_a_dry_run_reports_the_payload_without_touching_the_browser(monkeypatch):
    def _no_connect(**kwargs):
        raise AssertionError("a dry-run must not connect to a browser")

    _install(monkeypatch, _stub(connect=_no_connect))

    result = CliRunner().invoke(cli, [
        "browser", "exec", "--code", "document.title", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["executed"] is False
    assert payload["dry_run"] is True
    assert payload["would_execute"] is True
    assert payload["code_chars"] == len("document.title")
    assert payload["code_sha1"] == _sha1("document.title")
    assert payload["target_url"] == ""


def test_go_runs_the_code_and_returns_its_value(monkeypatch):
    seen = {}

    def _exec_js(conn, expression, **kwargs):
        seen["expression"] = expression
        seen.update(kwargs)
        return {"rows": 3}

    _install(monkeypatch, _stub(exec_js=_exec_js))

    result = CliRunner().invoke(cli, [
        "browser", "exec", "--code", "1 + 1", "--go", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["executed"] is True
    assert payload["dry_run"] is False
    assert payload["result"] == {"rows": 3}
    assert payload["target_url"] == "https://example.test/page"
    assert seen["expression"] == "1 + 1"


def test_the_source_may_come_from_a_file(monkeypatch, tmp_path):
    seen = {}
    script = tmp_path / "collect.js"
    script.write_text(
        "var rows = document.querySelectorAll('tr');\n"
        "rows.length;\n",
        encoding="utf-8",
    )

    def _exec_js(conn, expression, **kwargs):
        seen["expression"] = expression
        return 7

    _install(monkeypatch, _stub(exec_js=_exec_js))

    result = CliRunner().invoke(cli, [
        "browser", "exec", "--file", str(script), "--go", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["result"] == 7
    assert payload["code_sha1"] == _sha1(script.read_text(encoding="utf-8"))
    assert seen["expression"] == script.read_text(encoding="utf-8")


def test_await_promise_and_timeout_reach_the_backend(monkeypatch):
    seen = {}

    def _exec_js(conn, expression, **kwargs):
        seen.update(kwargs)
        return None

    _install(monkeypatch, _stub(exec_js=_exec_js))

    result = CliRunner().invoke(cli, [
        "browser", "exec", "--code", "fetch('/x')", "--go",
        "--await-promise", "--timeout", "5", "--json"])

    assert result.exit_code == 0, result.output
    assert seen["await_promise"] is True
    assert seen["timeout"] == 5.0


@pytest.mark.parametrize("argv", [
    ["browser", "exec", "--json"],
    ["browser", "exec", "--code", "1", "--file", "x.js", "--json"],
    ["browser", "exec", "--code", "   ", "--json"],
])
def test_a_bad_source_is_a_parameter_error(argv):
    result = CliRunner().invoke(cli, argv)
    assert result.exit_code == 2, result.output


def test_a_page_error_names_its_cause(monkeypatch):
    def _exec_js(conn, expression, **kwargs):
        raise RuntimeError("ReferenceError: nope is not defined")

    _install(monkeypatch, _stub(exec_js=_exec_js))

    result = CliRunner().invoke(cli, [
        "browser", "exec", "--code", "nope()", "--go", "--json"])

    assert result.exit_code == 1, result.output
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert "page JavaScript failed" in payload["error"]
    assert "nope is not defined" in payload["error"]
    assert payload["code_sha1"] == _sha1("nope()")


def test_a_mismatched_page_fails_closed(monkeypatch):
    def _connect(**kwargs):
        raise RuntimeError("no page target matched url_contains='other'")

    _install(monkeypatch, _stub(connect=_connect))

    result = CliRunner().invoke(cli, [
        "browser", "exec", "--code", "1", "--go",
        "--url-contains", "other", "--json"])

    assert result.exit_code == 4, result.output
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert "url_contains" in payload["error"]


# --------------------------------------------------------------- MCP side

def _mcp_call(name, arguments):
    return json.loads(asyncio.run(mcp_server.call_tool(name, arguments))[0].text)


def test_mcp_browser_exec_dry_run_does_not_execute(monkeypatch):
    def _no_connect(**kwargs):
        raise AssertionError("a dry-run must not connect to a browser")

    monkeypatch.setattr(
        mcp_server, "browser_backend",
        types.SimpleNamespace(connect=_no_connect),
    )

    payload = _mcp_call("browser_exec", {"code": "document.title"})
    assert payload["executed"] is False
    assert payload["dry_run"] is True
    assert payload["would_execute"] is True
    assert payload["code_sha1"] == _sha1("document.title")


def test_mcp_browser_exec_runs_the_code_when_asked(monkeypatch):
    backend = types.SimpleNamespace(
        connect=lambda **kwargs: _Conn(),
        exec_js=lambda conn, expression, **kwargs: "ok",
    )
    monkeypatch.setattr(mcp_server, "browser_backend", backend)

    payload = _mcp_call("browser_exec", {"code": "1 + 1", "go": True})
    assert payload["executed"] is True
    assert payload["result"] == "ok"


def test_mcp_browser_exec_dry_run_wins_over_go(monkeypatch):
    def _no_exec_js(conn, expression, **kwargs):
        raise AssertionError("dry_run=true must not run the code")

    backend = types.SimpleNamespace(
        connect=lambda **kwargs: _Conn(),
        exec_js=_no_exec_js,
    )
    monkeypatch.setattr(mcp_server, "browser_backend", backend)

    payload = _mcp_call(
        "browser_exec", {"code": "1 + 1", "go": True, "dry_run": True})
    assert payload["executed"] is False
    assert payload["dry_run"] is True


def test_mcp_browser_exec_needs_exactly_one_source():
    payload = _mcp_call("browser_exec", {})
    assert "code or code_file" in payload["error"]
