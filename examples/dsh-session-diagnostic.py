"""Capture dsh web session and console diagnostics over CDP."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO

import websocket

from openeyes.backends.cdp import CDPError, list_tabs


SESSION_MARKERS = (
    "session.create",
    "session_create",
    "session/create",
    "sessionId",
    "session_id",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_line(record: dict[str, Any]) -> str:
    return json.dumps(record, ensure_ascii=False, sort_keys=True)


def _remote_value(argument: dict[str, Any]) -> Any:
    if "value" in argument:
        return argument["value"]
    if "unserializableValue" in argument:
        return argument["unserializableValue"]
    return argument.get("description", argument.get("type", ""))


def _contains_session_marker(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    lowered = value.lower()
    return any(marker.lower() in lowered for marker in SESSION_MARKERS)


def _storage_expression() -> str:
    return (
        "(() => {"
        "const keys = Object.keys(localStorage).filter((key) => "
        "key.includes('dsh.sessions') || key.includes('session'));"
        "return {value: localStorage.getItem('dsh.sessions.current'), keys};"
        "})()"
    )


class DiagnosticSession:
    def __init__(self, ws_url: str, *, output: TextIO | None = None) -> None:
        self.ws = websocket.create_connection(
            ws_url,
            timeout=1.0,
            origin="http://127.0.0.1",
        )
        self.output = output
        self.next_id = 0
        self.matching_request_ids: set[str] = set()
        self.last_storage: Any = object()

    def emit(self, kind: str, payload: Any) -> None:
        record = {"at": _now(), "kind": kind, "payload": payload}
        line = _json_line(record)
        print(line, flush=True)
        if self.output is not None:
            self.output.write(line + "\n")
            self.output.flush()

    def request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        self.next_id += 1
        request_id = self.next_id
        self.ws.send(json.dumps({"id": request_id, "method": method, "params": params or {}}))
        while True:
            raw = self.ws.recv()
            if not raw:
                raise CDPError("empty CDP response for " + method)
            message = json.loads(raw)
            if message.get("id") == request_id:
                if "error" in message:
                    raise CDPError(method + ": " + str(message["error"]))
                return message.get("result", {})
            self.handle_message(message)

    def evaluate(self, expression: str) -> Any:
        result = self.request(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True},
        ).get("result", {})
        return result.get("value")

    def handle_message(self, message: dict[str, Any]) -> None:
        method = message.get("method")
        params = message.get("params", {})
        if method == "Runtime.consoleAPICalled":
            self.emit(
                "console",
                {
                    "type": params.get("type"),
                    "args": [_remote_value(arg) for arg in params.get("args", [])],
                    "stack": params.get("stackTrace", {}),
                },
            )
            return
        if method == "Runtime.exceptionThrown":
            self.emit("exception", params.get("exceptionDetails", {}))
            return
        if method == "Network.requestWillBeSent":
            request = params.get("request", {})
            post_data = request.get("postData", "")
            if _contains_session_marker(request.get("url")) or _contains_session_marker(post_data):
                request_id = str(params.get("requestId", ""))
                self.matching_request_ids.add(request_id)
                self.emit(
                    "session_request",
                    {
                        "request_id": request_id,
                        "url": request.get("url"),
                        "method": request.get("method"),
                        "post_data": post_data,
                    },
                )
            return
        if method == "Network.responseReceived":
            response = params.get("response", {})
            request_id = str(params.get("requestId", ""))
            if request_id in self.matching_request_ids or _contains_session_marker(response.get("url")):
                self.matching_request_ids.add(request_id)
                self.emit(
                    "session_response",
                    {
                        "request_id": request_id,
                        "url": response.get("url"),
                        "status": response.get("status"),
                        "mime_type": response.get("mimeType"),
                    },
                )
            return
        if method == "Network.loadingFinished":
            request_id = str(params.get("requestId", ""))
            if request_id in self.matching_request_ids:
                try:
                    body = self.request("Network.getResponseBody", {"requestId": request_id})
                except (CDPError, OSError, websocket.WebSocketException) as exc:
                    self.emit("session_response_body_error", {"request_id": request_id, "error": str(exc)})
                else:
                    self.emit("session_response_body", {"request_id": request_id, "body": body})
            return
        if method in ("Network.webSocketFrameSent", "Network.webSocketFrameReceived"):
            frame = params.get("response", {})
            payload = frame.get("payloadData", "")
            if _contains_session_marker(payload):
                self.emit(
                    "session_websocket_frame",
                    {
                        "direction": "sent" if method.endswith("Sent") else "received",
                        "request_id": params.get("requestId"),
                        "payload": payload,
                    },
                )

    def poll_storage(self) -> None:
        value = self.evaluate(_storage_expression())
        if value != self.last_storage:
            self.last_storage = value
            self.emit("dsh_sessions_current", value)

    def close(self) -> None:
        self.ws.close()


def _select_target(port: int, url_contains: str) -> dict[str, Any]:
    pages = list_tabs(port)
    matches = [page for page in pages if url_contains in (page.get("url") or "")]
    if not matches:
        available = ", ".join(repr(page.get("url") or "") for page in pages)
        raise CDPError(f"no page target matched url_contains={url_contains!r}; available URLs: {available}")
    target = matches[0]
    ws_url = target.get("webSocketDebuggerUrl")
    if not ws_url:
        raise CDPError("target has no webSocketDebuggerUrl")
    if ws_url.startswith("ws://127.0.0.1/"):
        target["webSocketDebuggerUrl"] = ws_url.replace(
            "ws://127.0.0.1/", f"ws://127.0.0.1:{port}/", 1
        )
    elif ws_url.startswith("ws://localhost/"):
        target["webSocketDebuggerUrl"] = ws_url.replace(
            "ws://localhost/", f"ws://127.0.0.1:{port}/", 1
        )
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=9222)
    parser.add_argument("--url-contains", required=True)
    parser.add_argument("--seconds", type=float, default=120.0)
    parser.add_argument("--out", type=Path, help="optional JSONL output path")
    args = parser.parse_args()

    output = None
    diagnostic = None
    try:
        target = _select_target(args.port, args.url_contains)
        if args.out is not None:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            output = args.out.open("w", encoding="utf-8", newline="\n")
        diagnostic = DiagnosticSession(target["webSocketDebuggerUrl"], output=output)
        diagnostic.request("Runtime.enable")
        diagnostic.request("Network.enable")
        diagnostic.emit(
            "attached",
            {"url": target.get("url"), "title": target.get("title"), "target_id": target.get("id")},
        )
        diagnostic.poll_storage()
        deadline = time.monotonic() + args.seconds
        next_poll = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            if time.monotonic() >= next_poll:
                diagnostic.poll_storage()
                next_poll = time.monotonic() + 1.0
            try:
                raw = diagnostic.ws.recv()
            except websocket.WebSocketTimeoutException:
                continue
            if raw:
                diagnostic.handle_message(json.loads(raw))
    except KeyboardInterrupt:
        return 0
    except (CDPError, OSError, ValueError, websocket.WebSocketException) as exc:
        print(json.dumps({"ready": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    finally:
        if diagnostic is not None:
            diagnostic.close()
        if output is not None:
            output.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
