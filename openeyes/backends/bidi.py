"""Drive a running Firefox Remote Agent over WebDriver BiDi.

Firefox exposes a pure BiDi WebSocket at ``/session`` rather than the
Chromium ``/json`` CDP discovery endpoints, so this backend speaks BiDi
directly instead of borrowing Puppeteer. The public surface mirrors the CDP
backend (``list_tabs`` / ``connect`` / ``scan_dom`` / ``click_center`` /
``type_text`` / ``screenshot``) so callers switch protocol, not API.
"""
from __future__ import annotations

import atexit
import base64
import json
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

import websocket

from openeyes.backends.dom_probe import control_type_map, dom_js, make_element

BIDI_DEFAULT_PORT = 9222
# Firefox Remote Agent checks the HTTP Host header against
# ``--remote-allow-hosts``. The X240 profile allows localhost, so keep this
# spelling even when the socket is reached through a local SSH forward.
BIDI_LOCALHOST = "localhost"


class BidiError(RuntimeError):
    """Raised when Firefox Remote Agent cannot complete a BiDi operation."""


class BidiConnection:
    """Synchronous WebDriver BiDi connection to one Firefox instance.

    The connection owns a WebDriver session. Closing it ends that session
    only - the Firefox window the user is working in stays open.
    """

    def __init__(self, ws_url: str, *, timeout: float = 30.0,
                 host_header: str | None = None) -> None:
        self.ws_url = ws_url
        self._id = 0
        self._timeout = timeout
        if host_header and any(char in host_header for char in "\r\n"):
            raise ValueError("host_header must not contain CR or LF")
        options: dict[str, Any] = {
            "timeout": timeout,
            "suppress_origin": True,
        }
        if host_header:
            # The socket may be a local SSH forward while Firefox checks the
            # original Remote Agent port in its HTTP Host allow-list.
            options["host"] = host_header
        # Firefox 159 accepts the BiDi wire without echoing a WebSocket
        # subprotocol. Asking websocket-client to enforce one makes it
        # reject Firefox's otherwise valid 101 response.
        self._ws = websocket.create_connection(ws_url, **options)
        self.session_id = ""
        self.contexts: list[dict[str, Any]] = []
        self.context_id = ""
        self._elements: list[tuple[str, Any]] = []
        self._closed = False
        atexit.register(self.close)
        try:
            self._start_session()
            self._refresh_contexts()
        except Exception:
            self.close()
            raise

    # ----- protocol plumbing ------------------------------------------

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    def call(self, method: str, params: dict[str, Any] | None = None,
             timeout: float | None = None) -> dict[str, Any]:
        msg_id = self._next_id()
        self._ws.send(json.dumps({
            "id": msg_id,
            "method": method,
            "params": params or {},
        }))
        self._ws.settimeout(timeout or self._timeout)
        while True:
            raw = self._ws.recv()
            if not raw:
                raise BidiError("empty response for " + method)
            message = json.loads(raw)
            if message.get("id") != msg_id:
                # BiDi events arrive while a command is in flight.
                continue
            if message.get("type") == "error":
                error = str(message.get("error") or "remote error")
                detail = message.get("message")
                if detail:
                    error += ": " + str(detail)
                raise BidiError(method + ": " + error)
            return message.get("result") or {}

    def _start_session(self) -> None:
        result = self.call("session.new", {
            "capabilities": {
                "alwaysMatch": {
                    "acceptInsecureCerts": False,
                    "webSocketUrl": True,
                }
            }
        })
        self.session_id = result.get("sessionId") or ""
        if not self.session_id:
            raise BidiError("session.new returned no sessionId")

    def _refresh_contexts(self) -> list[dict[str, Any]]:
        result = self.call("browsingContext.getTree", {})
        flat: list[dict[str, Any]] = []

        def walk(nodes: list[dict[str, Any]], parent: str | None) -> None:
            for node in nodes:
                context_id = node.get("context") or ""
                if not context_id:
                    continue
                flat.append({
                    "context": context_id,
                    "url": node.get("url") or "",
                    "parent": parent,
                })
                walk(node.get("children") or [], context_id)

        walk(result.get("contexts") or [], None)
        self.contexts = flat
        if not self.contexts:
            raise BidiError("Firefox has no browsing contexts")
        if not self.context_id:
            self.context_id = self.contexts[0]["context"]
        return self.contexts

    def _context(self, context_id: str | None = None) -> str:
        context = context_id or self.context_id
        if not context:
            raise BidiError("no browsing context selected")
        return context

    # ----- reading ----------------------------------------------------

    def evaluate(self, expression: str, await_promise: bool = False,
                 timeout: float = 20.0, context_id: str | None = None) -> Any:
        # JSON.stringify gives BiDi object/array results the same plain Python
        # shape that CDP Runtime.evaluate(returnByValue=true) gives OpenEyes.
        wrapped = "JSON.stringify((" + expression + "))"
        result = self.call("script.evaluate", {
            "expression": wrapped,
            "target": {"context": self._context(context_id)},
            "awaitPromise": await_promise,
            "resultOwnership": "none",
        }, timeout=timeout)
        remote = result.get("result") or {}
        if remote.get("type") == "exception":
            details = remote.get("exceptionDetails") or {}
            raise BidiError(
                "script.evaluate failed: "
                + str(details.get("text") or remote)
            )
        if "value" not in remote:
            return None
        value = remote.get("value")
        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return value
        return value

    def current_url(self) -> str:
        for context in self.contexts:
            if context["context"] == self.context_id:
                return context["url"]
        return ""

    def page_title(self) -> str:
        value = self.evaluate("document.title")
        return value if isinstance(value, str) else ""

    def tabs(self) -> list[dict[str, Any]]:
        out = []
        for context in self.contexts:
            try:
                title = self.page_title_for(context["context"])
            except BidiError:
                title = ""
            out.append({
                "id": context["context"],
                "title": title,
                "url": context["url"],
                "type": "page",
            })
        return out

    def page_title_for(self, context_id: str) -> str:
        value = self.evaluate("document.title", context_id=context_id)
        return value if isinstance(value, str) else ""

    # ----- acting -----------------------------------------------------

    def navigate(self, url: str, wait_ms: int = 600) -> None:
        self.call("browsingContext.navigate", {
            "context": self._context(),
            "url": url,
            "wait": "complete",
        })
        time.sleep(wait_ms / 1000.0)
        self._refresh_contexts()

    def click_at(self, x: int, y: int, context_id: str | None = None) -> None:
        self.call("input.performActions", {
            "context": self._context(context_id),
            "actions": [{
                "type": "pointer",
                "id": "openeyes-mouse",
                "parameters": {"pointerType": "mouse"},
                "actions": [
                    {"type": "pointerMove", "x": int(x), "y": int(y),
                     "origin": "viewport"},
                    {"type": "pointerDown", "button": 0},
                    {"type": "pointerUp", "button": 0},
                ],
            }],
        })

    def key_actions(self, actions: list[dict[str, Any]],
                    context_id: str | None = None) -> None:
        if not actions:
            return
        self.call("input.performActions", {
            "context": self._context(context_id),
            "actions": [{
                "type": "key",
                "id": "openeyes-keyboard",
                "actions": actions,
            }],
        })

    def insert_text(self, text: str, context_id: str | None = None) -> bool:
        """Use input.insertText when the browser supports it.

        insertText handles non-ASCII input directly; per-character key events
        cannot type CJK text into most fields. Returns False when the
        browser does not implement the command.
        """
        try:
            self.call("input.insertText", {
                "context": self._context(context_id),
                "text": text,
            })
            return True
        except BidiError as exc:
            message = str(exc).lower()
            if "no such command" in message or "unknown command" in message:
                return False
            raise

    def type_text(self, text: str, press_enter: bool = False,
                  context_id: str | None = None) -> None:
        enter = [
            {"type": "keyDown", "value": "\uE007"},
            {"type": "keyUp", "value": "\uE007"},
        ]
        if text and self.insert_text(text, context_id=context_id):
            if press_enter:
                self.key_actions(enter, context_id=context_id)
            return
        actions: list[dict[str, Any]] = []
        for char in text:
            actions.append({"type": "keyDown", "value": char})
            actions.append({"type": "keyUp", "value": char})
        if press_enter:
            actions.extend(enter)
        self.key_actions(actions, context_id=context_id)

    def screenshot(self, out_path: str, context_id: str | None = None) -> str:
        result = self.call("browsingContext.captureScreenshot", {
            "context": self._context(context_id),
            "origin": "viewport",
            "format": {"type": "image/png"},
        })
        data = result.get("data")
        if not isinstance(data, str):
            raise BidiError("captureScreenshot returned no image data")
        path = Path(out_path).absolute()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(base64.b64decode(data))
        return str(path)

    def close(self) -> None:
        """End the WebDriver session; Firefox itself keeps running."""
        if getattr(self, "_closed", False):
            return
        self._closed = True
        atexit.unregister(self.close)
        try:
            if self.session_id:
                self.call("session.end", {}, timeout=5.0)
        except Exception:
            pass
        with suppress(Exception):
            self._ws.close()

    # ----- element bookkeeping ---------------------------------------

    def remember_element(self, context_id: str, element: Any) -> None:
        self._elements.append((context_id, element))

    def context_of(self, element: Any) -> str:
        for context_id, known in self._elements:
            if known is element:
                return context_id
        return self.context_id


# ----- module-level API (mirrors the CDP backend) ---------------------


def _endpoint(port: int) -> str:
    return "ws://" + BIDI_LOCALHOST + ":" + str(port) + "/session"


def connect(port: int = BIDI_DEFAULT_PORT, url_contains: str | None = None,
            tab_index: int = 0, timeout: float = 30.0,
            host_header: str | None = None) -> BidiConnection:
    """Open a BiDi session against a running Firefox Remote Agent."""
    conn = BidiConnection(_endpoint(port), timeout=timeout,
                          host_header=host_header)
    if url_contains:
        matching = [
            context for context in conn.contexts
            if url_contains in context["url"]
        ]
        if not matching:
            available = ", ".join(repr(c["url"]) for c in conn.contexts)
            conn.close()
            raise BidiError(
                "no page target matched url_contains=" + repr(url_contains)
                + "; available URLs: " + available
            )
        conn.context_id = matching[0]["context"]
    else:
        index = max(0, min(tab_index, len(conn.contexts) - 1))
        conn.context_id = conn.contexts[index]["context"]
    return conn


def list_tabs(port: int = BIDI_DEFAULT_PORT,
              host_header: str | None = None) -> list[dict[str, Any]]:
    conn = connect(port=port, host_header=host_header)
    try:
        return conn.tabs()
    finally:
        conn.close()


def scan_dom(conn: BidiConnection, *, filter_url_contains=None,
             limit: int = 500) -> list:
    """Probe the selected Firefox page and return its interactive elements."""
    out: list = []
    context = next(
        (item for item in conn.contexts
         if item["context"] == conn.context_id),
        None,
    )
    if context is None:
        return out
    if filter_url_contains and filter_url_contains not in context["url"]:
        return out
    try:
        raw = conn.evaluate(dom_js("viewport"), context_id=context["context"])
    except BidiError:
        return out
    if not isinstance(raw, list):
        return out
    for record in raw[:max(0, limit - len(out))]:
        record = dict(record)
        record["control_type"] = control_type_map(record)
        element = make_element(record, backend="bidi")
        conn.remember_element(context["context"], element)
        out.append(element)
    return out


def _selector_of(element: Any) -> str:
    automation_id = getattr(element, "automation_id", "") or ""
    if automation_id:
        return "#" + automation_id
    class_name = (getattr(element, "class_name", "") or "").split()
    if class_name:
        return "." + class_name[0]
    return element.control_type.lower()


_FOCUS_AT_JS = """(function(x, y) {
  var el = document.elementFromPoint(x, y);
  if (!el) return false;
  if (!el.matches("input,textarea,select,[contenteditable],[role=textbox]")) {
    el = el.closest("input,textarea,select,[contenteditable],[role=textbox]");
  }
  if (!el) return false;
  try { el.focus(); } catch (e) {}
  if (el.setSelectionRange && el.value !== undefined) {
    try { el.setSelectionRange(el.value.length, el.value.length); } catch (e) {}
  }
  return document.activeElement === el;
})(__X__, __Y__)"""


def focus_element(conn: BidiConnection, element: Any) -> bool:
    expression = _FOCUS_AT_JS.replace("__X__", str(int(element.center.x)))
    expression = expression.replace("__Y__", str(int(element.center.y)))
    try:
        result = conn.evaluate(expression, context_id=conn.context_of(element))
    except BidiError:
        return False
    return bool(result)


def click_center(conn: BidiConnection, element: Any) -> None:
    conn.click_at(element.center.x, element.center.y,
                  context_id=conn.context_of(element))


def type_text(conn: BidiConnection, text: str, element=None,
              press_enter: bool = False) -> None:
    context_id = conn.context_of(element) if element is not None else conn.context_id
    if element is not None and not focus_element(conn, element):
        raise BidiError("could not focus element: " + repr(element.name))
    conn.type_text(text, press_enter=press_enter, context_id=context_id)


def screenshot(conn: BidiConnection, out_path: str) -> str:
    return conn.screenshot(out_path)
