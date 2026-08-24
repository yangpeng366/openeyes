"""Detect the dsh web client fetch-stall symptom over CDP (read-only).

Background
==========
The dsh web host at http://127.0.0.1:3080/ serves the UI and a JSON-RPC API.
Initial page load completes, but every ``fetch()`` from the page context after
that hangs indefinitely. A direct PowerShell request to the same endpoint
returns in single-digit milliseconds, so the network is healthy. The
JavaScript event loop is responsive (``Promise.resolve()`` returns normally),
and no service worker is registered.

This probe is a lightweight, read-only check that reproduces the symptom
without taking any side-effecting action. It is the recommended precheck
before launching the longer 180-second session-create diagnostic.

Safety
======
This script only attaches to a CDP-enabled page that already exists, enables
the ``Runtime`` and ``Network`` domains, and evaluates three small expressions:

1. ``Promise.resolve(1).then((v) => ({ok: true, value: v}))`` — verifies the JS
   event loop responds.
2. ``fetch('/')`` with an ``AbortController`` timeout — tries
   one outbound request from the page context.
3. A read of ``localStorage['dsh.sessions.current']``.

It does NOT click, type, navigate, focus, send keys, mutate storage, launch
a browser, or write any file other than the optional JSON output.

Usage
=====
::

    python examples/dsh-fetch-stall-probe.py --url-contains 127.0.0.1:3080
    python examples/dsh-fetch-stall-probe.py --url-contains 127.0.0.1:3080 --timeout 5 --out "$env:TEMP/openeyes-dsh-fetch-stall.json"

Exit ``0`` = no stall detected. Exit ``2`` = page-context fetch stalled while
the PowerShell fetch succeeded (the dsh fetch-stall symptom). Exit ``3`` = CDP
attach itself failed.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO

import websocket

from openeyes.backends.cdp import CDPError, list_tabs


FETCH_PATH = "/"
FETCH_EXPRESSION_TEMPLATE = """
(() => {{
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(new Error('probe-timeout')), {timeout_ms});
    const start = performance.now();
    return fetch({fetch_path!r}, {{ signal: ctrl.signal, credentials: 'same-origin' }})
        .then((resp) => {{
            clearTimeout(timer);
            return {{
                ok: resp.ok,
                status: resp.status,
                elapsed_ms: Math.round(performance.now() - start),
            }};
        }})
        .catch((err) => {{
            clearTimeout(timer);
            return {{
                ok: false,
                status: 0,
                error: String(err && err.message ? err.message : err),
                elapsed_ms: Math.round(performance.now() - start),
            }};
        }});
}})()
"""

STORAGE_EXPRESSION = (
    "(() => {"
    "const keys = Object.keys(localStorage).filter((k) => k.includes('dsh.sessions') || k.includes('session'));"
    "return {value: localStorage.getItem('dsh.sessions.current'), keys};"
    "})()"
)

EVENT_LOOP_EXPRESSION = "Promise.resolve(1).then((v) => ({ok: true, value: v}))"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_line(record):
    return json.dumps(record, ensure_ascii=False, sort_keys=True)


def _resolve_ws_url(target, port):
    ws_url = target.get("webSocketDebuggerUrl") or ""
    if ws_url.startswith("ws://127.0.0.1/"):
        return ws_url.replace("ws://127.0.0.1/", f"ws://127.0.0.1:{port}/", 1)
    if ws_url.startswith("ws://localhost/"):
        return ws_url.replace("ws://localhost/", f"ws://127.0.0.1:{port}/", 1)
    return ws_url


def _select_target(port, url_contains):
    pages = list_tabs(port)
    matches = [page for page in pages if url_contains in (page.get("url") or "")]
    if not matches:
        available = ", ".join(repr(page.get("url") or "") for page in pages)
        raise CDPError(f"no page target matched url_contains={url_contains!r}; available: {available}")
    target = dict(matches[0])
    target["webSocketDebuggerUrl"] = _resolve_ws_url(target, port)
    return target


def _request(ws, method, params, request_id, *, recv_timeout):
    ws.send(json.dumps({"id": request_id, "method": method, "params": params}))
    deadline = time.monotonic() + recv_timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CDPError(f"{method}: receive timeout after {recv_timeout:.1f}s")
        ws.settimeout(remaining)
        try:
            raw = ws.recv()
        except websocket.WebSocketTimeoutException as exc:
            raise CDPError(f"{method}: receive timeout after {recv_timeout:.1f}s: {exc}") from exc
        if not raw:
            raise CDPError("empty CDP response for " + method)
        message = json.loads(raw)
        if message.get("id") == request_id:
            if "error" in message:
                raise CDPError(method + ": " + str(message["error"]))
            return message.get("result", {})


def _evaluate(ws, expression, request_id, *, recv_timeout):
    result = _request(
        ws,
        "Runtime.evaluate",
        {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": True,
        },
        request_id,
        recv_timeout=recv_timeout,
    )
    outcome = result.get("result", {})
    if "exceptionDetails" in result:
        raise CDPError("page expression raised: " + json.dumps(result["exceptionDetails"], ensure_ascii=False))
    return outcome


def _power_shell_fetch(url, timeout):
    start = time.monotonic()
    try:
        req = urllib.request.Request(url, headers={"Host": "127.0.0.1"})
        with urllib.request.urlopen(req, timeout=timeout) as response:
            status = getattr(response, "status", 0)
            elapsed_ms = int((time.monotonic() - start) * 1000)
            return {"ok": True, "status": status, "elapsed_ms": elapsed_ms}
    except urllib.error.HTTPError as exc:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return {"ok": False, "status": exc.code, "error": str(exc), "elapsed_ms": elapsed_ms}
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return {"ok": False, "status": 0, "error": str(exc), "elapsed_ms": elapsed_ms}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=9222)
    parser.add_argument("--url-contains", required=True, help="substring of the dsh web page URL")
    parser.add_argument(
        "--timeout",
        type=float,
        default=5.0,
        help="timeout in seconds for page-context evaluate and PowerShell fetch",
    )
    parser.add_argument(
        "--connect-timeout",
        type=float,
        default=2.0,
        help="WebSocket connect timeout in seconds",
    )
    parser.add_argument("--fetch-path", default=FETCH_PATH, help="path passed to page-context fetch()")
    parser.add_argument("--origin", default="http://127.0.0.1:3080", help="Origin passed to the CDP Origin header. Also used for the PowerShell comparison fetch.")
    parser.add_argument("--out", type=Path, help="optional JSON output path")
    args = parser.parse_args()

    output = None
    ws = None
    try:
        target = _select_target(args.port, args.url_contains)
        if args.out is not None:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            output = args.out.open("w", encoding="utf-8", newline="\n")

        def _emit(record):
            line = _json_line(record)
            print(line, flush=True)
            if output is not None:
                output.write(line + "\n")
                output.flush()

        _emit(
            {
                "at": _now(),
                "kind": "attached",
                "payload": {
                    "url": target.get("url"),
                    "title": target.get("title"),
                    "target_id": target.get("id"),
                    "webSocketDebuggerUrl": target.get("webSocketDebuggerUrl"),
                },
            }
        )

        ws = websocket.create_connection(
            target["webSocketDebuggerUrl"],
            timeout=args.connect_timeout,
            origin=args.origin,
        )
        # After connect, switch to the user-controlled timeout for recv so awaited
        # promises (Runtime.evaluate with awaitPromise) have enough time to resolve.
        ws.settimeout(args.timeout)

        _request(ws, "Runtime.enable", {}, 1, recv_timeout=args.timeout + 1)
        _request(ws, "Network.enable", {}, 2, recv_timeout=args.timeout + 1)

        event_loop = _evaluate(
            ws,
            EVENT_LOOP_EXPRESSION,
            3,
            recv_timeout=args.timeout + 1,
        ).get("value", {})

        fetch_expression = FETCH_EXPRESSION_TEMPLATE.format(
            timeout_ms=max(500, int(args.timeout * 1000) - 1000),
            fetch_path=args.fetch_path,
        )
        try:
            page_fetch = _evaluate(
                ws, fetch_expression, 4, recv_timeout=args.timeout + 1
            ).get("value", {})
        except CDPError as exc:
            if "receive timeout" in str(exc):
                # The page JavaScript event loop is blocked, so even the
                # AbortController.setTimeout did not fire. The recv timeout
                # is the only signal. Treat this as the fetch-stall symptom
                # rather than a CDP attach failure.
                page_fetch = {
                    "ok": False,
                    "status": 0,
                    "error": f"page fetch stalled: {exc}",
                    "elapsed_ms": int(args.timeout * 1000),
                }
            else:
                raise

        storage = _evaluate(ws, STORAGE_EXPRESSION, 5, recv_timeout=args.timeout + 1).get("value", {})

        parsed_origin = urllib.parse.urlsplit(args.origin)
        host = parsed_origin.hostname or "127.0.0.1"
        port = parsed_origin.port
        host_port = f"{host}:{port}" if port else host
        ps_url = f"http://{host_port}{args.fetch_path}"
        ps_fetch = _power_shell_fetch(ps_url, args.timeout)

        page_stalled = (not page_fetch.get("ok")) and page_fetch.get("status", 0) == 0
        fetch_stalled = page_stalled and ps_fetch.get("ok", False)

        summary = {
            "at": _now(),
            "kind": "summary",
            "payload": {
                "event_loop_ok": event_loop.get("ok") is True,
                "page_fetch": page_fetch,
                "ps_fetch": ps_fetch,
                "dsh_sessions_current": storage,
                "fetch_stalled": fetch_stalled,
            },
        }
        _emit(summary)
        return 2 if fetch_stalled else 0
    except (CDPError, OSError, ValueError, websocket.WebSocketException) as exc:
        print(json.dumps({"ready": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        return 0
    finally:
        if ws is not None:
            try:
                ws.close()
            except OSError:
                pass
        if output is not None:
            output.close()


if __name__ == "__main__":
    raise SystemExit(main())