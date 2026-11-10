"""Run the two-tab browser_type dry-run acceptance over direct MCP stdio.

This probe broadens the repository-local surrogate beyond ``browser_click`` so
the ``browser_type`` write contract is also accepted while the dsh web-host
gate remains closed. It mirrors ``browser-click-acceptance.py``:
self-contained transient HTTP fixture server, two disposable tabs via the CDP
``/json/new`` endpoint, then a dry-run + fail-closed check over MCP stdio.

A dry-run call must resolve the ``Learn more`` element (``sent:false``,
``would_send_chars`` populated, no text inserted); a selectorless dry-run must
return the exact ``target_url`` without resolving an element; and an unmatched
selectorless ``url_contains`` call must fail closed without proposing
``would_send_chars``/``target``.

The verdicts live in ``_require_type_request``, ``_require_type_dry_run``,
``_require_fail_closed_type`` and ``_select_acceptance_tabs`` so tests can feed
real MCP payloads and assert the values that decide pass/fail instead of
matching sentences in this file.
"""

from __future__ import annotations

import argparse
import contextlib
import functools
import http.server
import json
import queue
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLES_DIR = REPO_ROOT / "examples"
ACCEPTANCE_MARKER = "acceptance-pages"
TARGET_MARKER = "target-a"
TYPE_TEXT = "probe"
NAME_FILTER = "Learn more"
FOCUSED = "(focused)"
NO_PAGE_ERROR = "no page target matched url_contains"
NO_ELEMENT_ERROR = "no element matched the selector"


def _request(request_id: int, method: str, params: dict | None = None) -> str:
    message = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        message["params"] = params
    return json.dumps(message, ensure_ascii=False)


def _notification(method: str) -> str:
    return json.dumps(
        {"jsonrpc": "2.0", "method": method}, ensure_ascii=False
    )


def _read_stdout(stream, lines: queue.Queue[str]) -> None:
    for line in stream:
        if line.strip():
            lines.put(line)


def _stop_process(process: subprocess.Popen[str]) -> str:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    return process.stderr.read() if process.stderr else ""


def _result_content(result: dict) -> dict:
    content = result["content"]
    if len(content) != 1 or content[0].get("type") != "text":
        raise RuntimeError(f"unexpected tool result content: {content!r}")
    return json.loads(content[0]["text"])


def _type_arguments(
    port: int,
    *,
    text: str,
    url_contains: str,
    name_contains: str | None = None,
    press_enter: bool = False,
) -> dict:
    """Build the exact ``browser_type`` arguments the probe sends.

    ``dry_run`` is never sent, so the tool's own ``true`` default decides the
    contract. ``press_enter`` is only added when the probe means to submit,
    which keeps "no Enter key was dispatched" visible in the request itself.
    """
    arguments: dict = {
        "port": port,
        "url_contains": url_contains,
        "text": text,
    }
    if name_contains is not None:
        arguments["name_contains"] = name_contains
    if press_enter:
        arguments["press_enter"] = True
    return arguments


def _require_exact_text(arguments: dict, expected: str) -> str:
    """The characters handed to ``browser_type`` must be ``expected`` verbatim.

    Guards truncation (``prob``), case changes (``Probe``) and added whitespace
    (``probe ``). All three read as a pass while the probe only compared the
    character count, because ``would_send_chars`` stayed 5 in every case.
    """
    text = arguments.get("text")
    if not isinstance(text, str):
        raise RuntimeError(f"type request carried a non-string text: {text!r}")
    if text != expected:
        raise RuntimeError(
            f"type request text is not verbatim: expected {expected!r}, got {text!r}"
        )
    return text


def _require_type_request(
    arguments: dict,
    *,
    url_contains: str,
    name_contains: str | None,
    expected_text: str,
) -> dict:
    """The request must scope one tab, use the documented selector, submit nothing.

    ``name_contains=None`` is the selectorless branch: it must carry no
    ``hint``/``idx``/``control_type``, otherwise it would type into an element
    instead of the focused control it claims to exercise.
    """
    if arguments.get("url_contains") != url_contains:
        raise RuntimeError(f"type request scoped the wrong tab: {arguments!r}")
    if arguments.get("name_contains") != name_contains:
        raise RuntimeError(f"type request used the wrong selector: {arguments!r}")
    if any(key in arguments for key in ("hint", "idx", "control_type")):
        raise RuntimeError(
            f"type request carried an unexpected selector: {arguments!r}"
        )
    if arguments.get("press_enter"):
        raise RuntimeError(f"type request would dispatch the Enter key: {arguments!r}")
    if "dry_run" in arguments:
        raise RuntimeError(
            f"type request bypassed the dry-run default: {arguments!r}"
        )
    _require_exact_text(arguments, expected_text)
    return arguments


def _require_type_dry_run(
    payload: dict,
    *,
    text: str,
    into: str,
    target_url: str,
    target_name: str | None = None,
) -> dict:
    """A dry-run type must echo the text, resolve the named element, submit nothing.

    Guards five regressions that still read as a pass: the tool inserting text
    (``sent``/``sent_chars``), the readback count disagreeing with the text the
    probe sent, the selector resolving a different element (``into``), an
    Enter key being dispatched, and an error payload being scored as a resolved
    target.
    """
    if not isinstance(payload, dict):
        raise RuntimeError(f"browser_type returned a non-object payload: {payload!r}")
    if "error" in payload:
        raise RuntimeError(
            f"browser_type failed instead of resolving a target: {payload!r}"
        )
    if payload.get("dry_run") is not True or payload.get("sent") is not False:
        raise RuntimeError(f"matched call was not a dry-run: {payload!r}")
    if payload.get("sent_chars") != 0:
        raise RuntimeError(f"dry-run reported inserted characters: {payload!r}")
    if payload.get("would_send_chars") != len(text):
        raise RuntimeError(
            "readback mismatch: would_send_chars="
            f"{payload.get('would_send_chars')!r} for the {len(text)} "
            f"characters {text!r}"
        )
    if payload.get("into") != into:
        raise RuntimeError(f"matched call resolved wrong element: {payload!r}")
    if payload.get("press_enter") is not False:
        raise RuntimeError(f"dry-run would dispatch the Enter key: {payload!r}")
    if payload.get("target_url") != target_url:
        raise RuntimeError(f"matched call reported wrong target URL: {payload!r}")
    target = payload.get("target")
    if target_name is None:
        if target:
            raise RuntimeError(f"selectorless call resolved an element: {payload!r}")
    elif (target or {}).get("name") != target_name:
        raise RuntimeError(f"matched call resolved wrong element: {payload!r}")
    return payload


def _require_fail_closed_type(payload: dict, *, expected: str) -> str:
    """A refused type must name the miss and propose nothing to send."""
    if not isinstance(payload, dict):
        raise RuntimeError(f"missing call did not fail closed: {payload!r}")
    error = payload.get("error", "")
    if expected not in error:
        raise RuntimeError(f"missing call did not fail closed: {payload!r}")
    if payload.get("sent") is True or payload.get("sent_chars"):
        raise RuntimeError(f"missing call reported a send outcome: {payload!r}")
    leaked = [
        key
        for key in ("would_send_chars", "target", "target_url", "into")
        if payload.get(key)
    ]
    if leaked:
        raise RuntimeError(f"missing call proposed an action {leaked}: {payload!r}")
    return error


def _select_acceptance_tabs(
    tabs: list[dict], target_filter: str
) -> tuple[list[dict], dict, dict]:
    """Return the acceptance tabs plus exactly one target and one decoy tab.

    Edge session-restore can reopen previously closed fixture tabs, so the
    contract is "at least one of each" and the first match is used. The decoy
    half is required: without it the ``url_contains`` filter is never proven to
    discriminate, because every tab would satisfy it.
    """
    acceptance_tabs = [
        tab for tab in tabs if ACCEPTANCE_MARKER in (tab.get("url") or "")
    ]
    target_tabs = [
        tab for tab in acceptance_tabs if target_filter in (tab.get("url") or "")
    ]
    decoy_tabs = [
        tab for tab in acceptance_tabs if target_filter not in (tab.get("url") or "")
    ]
    if len(target_tabs) < 1 or len(decoy_tabs) < 1:
        raise RuntimeError(
            "expected at least one target-a and one decoy acceptance tab; "
            f"got targets={len(target_tabs)}, decoys={len(decoy_tabs)}, "
            f"tabs={tabs!r}"
        )
    return acceptance_tabs, target_tabs[0], decoy_tabs[0]


def _port_listening(port: int, host: str = "127.0.0.1",
                    timeout: float = 2.0) -> bool:
    import socket
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _start_fixture_server(
    directory: Path,
) -> tuple[http.server.ThreadingHTTPServer, int]:
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(directory)
    )
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, server.server_address[1]


def _open_tab(cdp_port: int, url: str) -> str:
    encoded = urllib.parse.quote(url, safe="")
    req = urllib.request.Request(
        f"http://127.0.0.1:{cdp_port}/json/new?{encoded}",
        method="PUT",
        headers={"Host": "127.0.0.1"},
    )
    with urllib.request.urlopen(req, timeout=10) as response:
        raw = response.read().decode("utf-8", errors="replace")
    return json.loads(raw).get("id", "") if raw.strip() else ""


def _close_tab(cdp_port: int, target_id: str) -> None:
    if not target_id:
        return
    req = urllib.request.Request(
        f"http://127.0.0.1:{cdp_port}/json/close/{target_id}",
        headers={"Host": "127.0.0.1"},
    )
    with contextlib.suppress(Exception):
        urllib.request.urlopen(req, timeout=5).read()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cdp-port", type=int, default=9222)
    parser.add_argument("--timeout", type=float, default=20.0)
    args = parser.parse_args()

    fixture_server = None
    opened_ids: list[str] = []
    process = subprocess.Popen(
        [sys.executable, "-m", "openeyes.mcp.server"],
        cwd=REPO_ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    lines: queue.Queue[str] = queue.Queue()
    reader = threading.Thread(
        target=_read_stdout,
        args=(process.stdout, lines),
        daemon=True,
    )
    reader.start()

    def call(request_id: int, method: str, params: dict | None = None) -> dict:
        assert process.stdin is not None
        process.stdin.write(_request(request_id, method, params) + "\n")
        process.stdin.flush()
        while True:
            message = json.loads(lines.get(timeout=args.timeout))
            if message.get("id") != request_id:
                continue
            if "error" in message:
                raise RuntimeError(f"{method} failed: {message['error']}")
            if "result" not in message:
                raise RuntimeError(f"{method} returned no result: {message!r}")
            return message["result"]

    try:
        if not _port_listening(args.cdp_port):
            raise RuntimeError(
                f"debug port {args.cdp_port} not listening - "
                f"start Edge with --remote-debugging-port={args.cdp_port}"
            )
        fixture_server, http_port = _start_fixture_server(EXAMPLES_DIR)
        probe_id = uuid.uuid4().hex
        target_filter = f"{TARGET_MARKER}.html?probe={probe_id}"
        missing_filter = f"missing-{probe_id}"
        target_url = (
            "http://127.0.0.1:"
            f"{http_port}/acceptance-pages/target-a.html?probe={probe_id}"
        )
        decoy_url = (
            "http://127.0.0.1:"
            f"{http_port}/acceptance-pages/decoy.html?probe={probe_id}"
        )
        opened_ids.append(_open_tab(args.cdp_port, target_url))
        opened_ids.append(_open_tab(args.cdp_port, decoy_url))
        time.sleep(0.8)

        call(
            1,
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "openeyes-browser-type-acceptance",
                    "version": "1",
                },
            },
        )
        assert process.stdin is not None
        process.stdin.write(_notification("notifications/initialized") + "\n")
        process.stdin.flush()

        tools = call(2, "tools/list")["tools"]
        tool_names = [tool["name"] for tool in tools]
        if "browser_tabs" not in tool_names or "browser_type" not in tool_names:
            raise RuntimeError(
                "MCP surface does not expose browser_tabs/browser_type"
            )

        tabs = _result_content(call(3, "tools/call", {
            "name": "browser_tabs",
            "arguments": {"port": args.cdp_port},
        }))
        acceptance_tabs, target_tab, _decoy_tab = _select_acceptance_tabs(
            tabs, target_filter
        )

        matched_arguments = _type_arguments(
            args.cdp_port,
            text=TYPE_TEXT,
            url_contains=target_filter,
            name_contains=NAME_FILTER,
        )
        _require_type_request(
            matched_arguments,
            url_contains=target_filter,
            name_contains=NAME_FILTER,
            expected_text=TYPE_TEXT,
        )
        matched = _result_content(call(4, "tools/call", {
            "name": "browser_type",
            "arguments": matched_arguments,
        }))
        _require_type_dry_run(
            matched,
            text=matched_arguments["text"],
            into=NAME_FILTER,
            target_name=NAME_FILTER,
            target_url=target_tab["url"],
        )

        focused_arguments = _type_arguments(
            args.cdp_port,
            text=TYPE_TEXT,
            url_contains=target_filter,
        )
        _require_type_request(
            focused_arguments,
            url_contains=target_filter,
            name_contains=None,
            expected_text=TYPE_TEXT,
        )
        focused = _result_content(call(5, "tools/call", {
            "name": "browser_type",
            "arguments": focused_arguments,
        }))
        _require_type_dry_run(
            focused,
            text=focused_arguments["text"],
            into=FOCUSED,
            target_url=target_url,
        )

        missing_arguments = _type_arguments(
            args.cdp_port,
            text=TYPE_TEXT,
            url_contains=missing_filter,
        )
        missing = _result_content(call(6, "tools/call", {
            "name": "browser_type",
            "arguments": missing_arguments,
        }))
        error = _require_fail_closed_type(missing, expected=NO_PAGE_ERROR)
    except (AssertionError, KeyError, OSError, queue.Empty, RuntimeError,
            TypeError, ValueError, json.JSONDecodeError) as exc:
        stderr = _stop_process(process)
        print(json.dumps({
            "passed": False,
            "error": str(exc),
            "stderr": stderr[-2000:],
        }, ensure_ascii=False))
        return 2
    finally:
        for target_id in opened_ids:
            _close_tab(args.cdp_port, target_id)
        if fixture_server is not None:
            fixture_server.shutdown()

    _stop_process(process)
    print(json.dumps({
        "passed": True,
        "transport": "mcp-stdio",
        "acceptance_tabs": len(acceptance_tabs),
        "target_url": target_tab["url"],
        "matched_type": matched,
        "selectorless_type": focused,
        "missing_type_error": error,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
