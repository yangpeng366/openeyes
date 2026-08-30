"""

Drive any Chromium browser over its remote-debugging WebSocket. Mirrors the
shape of openeyes.backends.uia so the rest of OpenEyes stays
backend-agnostic. The DOM probe is used so LLM never has to read pixels.

"""
from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Any

import websocket

from openeyes.core.schema import BBox, Center, Element, ElementState


# Connection constants
CDP_DEFAULT_PORT = 9222
CDP_LOCALHOST = "127.0.0.1"

EDGE_CANDIDATES = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)

EDGE_USER_DATA_LIVE = Path(os.environ.get("LOCALAPPDATA")) / "Microsoft" / "Edge" / "User Data"
DEFAULT_USER_DIR = Path(os.environ.get("TEMP")) / "openeyes-edge"


class CDPError(RuntimeError):
    """Raised when CDP returns an error or the socket breaks."""


def _edge_exe() -> str:
    override = os.environ.get("EDGE_EXE")
    if override:
        path = Path(override).expanduser()
        if path.is_file():
            return str(path)
        raise CDPError("EDGE_EXE does not point to a file: " + override)
    for cand in EDGE_CANDIDATES:
        if Path(cand).exists():
            return cand
    raise CDPError("Edge / Chrome not found. Set EDGE_EXE env or install Edge.")


def _http_get_json(url: str, timeout: float = 5.0) -> Any:
    req = urllib.request.Request(url, headers={"Host": "127.0.0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode("utf-8", errors="replace")
        if not raw.strip():
            return None
        if raw.startswith("\ufeff"):
            raw = raw.lstrip("\ufeff")
        return json.loads(raw)

# ===== Connection wrapper =====


class CDPConnection:
    """A single CDP WebSocket connection to one DevTools target."""

    def __init__(self, ws_url: str, *, timeout: float = 30.0) -> None:
        self.ws_url = ws_url
        self._id = 0
        self._ws = websocket.create_connection(ws_url, timeout=timeout, origin="http://127.0.0.1")

    def call(self, method: str, params=None, timeout: float = 20.0) -> Any:
        self._id += 1
        msg_id = self._id
        self._ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
        self._ws.settimeout(timeout)
        while True:
            raw = self._ws.recv()
            if not raw:
                raise CDPError("empty recv for " + method)
            obj = json.loads(raw)
            if obj.get("id") == msg_id:
                if "error" in obj:
                    raise CDPError(method + ": " + str(obj["error"]))
                return obj.get("result", {})

    def evaluate(self, expression: str, await_promise: bool = False, timeout: float = 20.0) -> Any:
        out = self.call("Runtime.evaluate", {"expression": expression, "returnByValue": True, "awaitPromise": await_promise}, timeout=timeout)
        result = out.get("result", {})
        if "exceptionDetails" in out:
            text = out["exceptionDetails"].get("text", "unknown")
            raise CDPError("Runtime.evaluate failed: " + text)
        return result.get("value")

    def navigate(self, url: str, wait_ms: int = 600) -> None:
        self.call("Page.enable")
        self.call("Page.navigate", {"url": url})
        time.sleep(wait_ms / 1000.0)

    def current_url(self) -> str:
        v = self.evaluate("location.href")
        return v if isinstance(v, str) else ""

    def page_title(self) -> str:
        v = self.evaluate("document.title")
        return v if isinstance(v, str) else ""

# ===== Discovery / connection =====


def list_targets(port: int = CDP_DEFAULT_PORT):
    return _http_get_json("http://" + CDP_LOCALHOST + ":" + str(port) + "/json") or []


def list_tabs(port: int = CDP_DEFAULT_PORT):
    return [t for t in list_targets(port) if t.get("type") == "page"]


def connect(port: int = CDP_DEFAULT_PORT, url_contains=None, tab_index: int = 0, timeout: float = 30.0):
    """Open a WebSocket to a running Chromium debug port."""
    pages = list_tabs(port)
    if not pages:
        raise CDPError("no page targets on port " + str(port))
    chosen = None
    if url_contains:
        for p in pages:
            if url_contains in (p.get("url") or ""):
                chosen = p
                break
        if chosen is None:
            raise CDPError(
                "no page target matched url_contains="
                + repr(url_contains)
                + "; available URLs: "
                + ", ".join(repr(p.get("url") or "") for p in pages)
            )
    if chosen is None:
        chosen = pages[max(0, min(tab_index, len(pages) - 1))]
    ws = chosen.get("webSocketDebuggerUrl")
    if not ws:
        raise CDPError("target has no webSocketDebuggerUrl")
    if ws.startswith("ws://127.0.0.1/") or ws.startswith("ws://localhost/"):
        ws = ws.replace("ws://127.0.0.1/", "ws://127.0.0.1:" + str(port) + "/", 1)
        ws = ws.replace("ws://localhost/", "ws://127.0.0.1:" + str(port) + "/", 1)
    return CDPConnection(ws, timeout=timeout)

# ===== Edge launcher =====


_PROFILE_SEED_FILES = (
    "Preferences", "Secure Preferences", "Cookies", "Login Data",
    "Web Data", "History",
)
_PROFILE_SEED_DIRS = (
    "Network", "Local Storage", "Session Storage", "IndexedDB",
)


def _copy_file(src, dst) -> bool:
    try:
        if not src.exists():
            return False
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        return True
    except OSError:
        return False


def _copy_dir(src, dst) -> bool:
    if not src.exists():
        return False
    try:
        dst.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dst, dirs_exist_ok=True)
        return True
    except OSError:
        return False


def seed_user_data(target, profile: str = "Default") -> None:
    """Copy live Edge session slices into a temp userDataDir."""
    target.mkdir(parents=True, exist_ok=True)
    _copy_file(EDGE_USER_DATA_LIVE / "Local State", target / "Local State")
    src_prof = EDGE_USER_DATA_LIVE / profile
    dst_prof = target / profile
    if src_prof.exists():
        dst_prof.mkdir(parents=True, exist_ok=True)
        for name in _PROFILE_SEED_FILES:
            _copy_file(src_prof / name, dst_prof / name)
        for name in _PROFILE_SEED_DIRS:
            _copy_dir(src_prof / name, dst_prof / name)


_SINGLETON_LOCK_NAMES = ("SingletonLock", "SingletonCookie", "SingletonSocket")


def _profile_is_disposable(profile_dir: Path) -> bool:
    """True when profile_dir lives under the system TEMP tree (safe to scrub).

    Only disposable profile dirs (the default temp user-data-dir or anything
    under %TEMP%) are ever touched by the stale-lock fallback; the live Edge
    profile is never modified.
    """
    try:
        temp = os.environ.get("TEMP") or os.environ.get("TMP") or ""
        if not temp:
            return False
        temp_root = Path(temp).resolve()
        resolved = profile_dir.resolve()
        return resolved == temp_root or temp_root in resolved.parents
    except OSError:
        return False


def _stale_singleton_locks(profile_dir: Path) -> list:
    """Return the Chromium singleton lock names present in profile_dir."""
    out = []
    for name in _SINGLETON_LOCK_NAMES:
        lock = profile_dir / name
        try:
            if lock.is_symlink() or lock.exists():
                out.append(name)
        except OSError:
            pass
    return out


def _clear_singleton_locks(profile_dir: Path) -> list:
    """Remove stale Chromium singleton lock files from a disposable profile dir."""
    removed = []
    for name in _SINGLETON_LOCK_NAMES:
        lock = profile_dir / name
        try:
            if lock.is_symlink() or lock.exists():
                lock.unlink()
                removed.append(name)
        except OSError:
            pass
    return removed


def _diagnose_launch(proc, port, profile_dir, last_err, *, proc_exited) -> str:
    """Build a bounded diagnostic string for a failed CDP launch."""
    parts = ["port=" + str(port)]
    parts.append("process=" + ("exited" if proc_exited else "alive"))
    if last_err is not None:
        parts.append("last_error=" + repr(last_err))
    locks = _stale_singleton_locks(profile_dir)
    if locks:
        parts.append("stale_locks=" + ",".join(locks))
    return "; ".join(parts)


def launch_edge(*, port: int = CDP_DEFAULT_PORT, url=None, profile_dir=None, seed: bool = True, headless: bool = False, wait_ms: int = 4500, retries: int = 1) -> dict:
    """Launch a dedicated Edge with remote debugging enabled.

    On failure the raised CDPError carries a bounded diagnostic (process
    state, last connection error, and any stale Chromium singleton locks in
    the profile dir) so a failed launch on a dedicated port (e.g. 9333) can
    be diagnosed without a second tool. When the spawned process exits early
    and the profile dir is disposable (under %TEMP%), a single bounded retry
    clears stale singleton locks before re-launching, so a crashed prior
    launch does not wedge the requested port. Disposable cleanup is
    preserved: only lock files inside the temp profile dir are touched, and
    the spawned process is best-effort terminated when it never opened CDP.
    """
    profile_dir = Path(profile_dir or DEFAULT_USER_DIR)
    attempts = 1 + max(0, retries)
    last_msg = "Edge did not expose CDP within " + str(wait_ms) + "ms"
    for attempt in range(attempts):
        if attempt > 0 and _profile_is_disposable(profile_dir):
            _clear_singleton_locks(profile_dir)
        if seed:
            seed_user_data(profile_dir)
        profile_dir.mkdir(parents=True, exist_ok=True)
        args = [
            _edge_exe(),
            "--remote-debugging-port=" + str(port),
            "--remote-allow-origins=*",
            "--user-data-dir=" + str(profile_dir),
            "--profile-directory=Default",
            "--no-first-run", "--no-default-browser-check",
            "--ignore-certificate-errors",
            "--disable-features=Translate,InfinitePrefetch",
            "--no-sandbox",
        ]
        if headless:
            args.append("--headless=new")
        if url:
            args.append(url)
        proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
        deadline = time.time() + max(5.0, wait_ms / 1000.0)
        last_err = None
        while time.time() < deadline:
            try:
                list_tabs(port)
                return {"port": port, "pid": proc.pid, "profile_dir": str(profile_dir)}
            except Exception as e:
                last_err = e
                time.sleep(0.4)
        proc_exited = proc.poll() is not None
        diag = _diagnose_launch(proc, port, profile_dir, last_err, proc_exited=proc_exited)
        try:
            if not proc_exited:
                proc.terminate()
                proc.wait(timeout=2.0)
        except Exception:
            pass
        last_msg = "Edge did not expose CDP within " + str(wait_ms) + "ms [" + diag + "]"
        # Bounded fallback: retry only when the process exited early (the
        # classic singleton-reuse / stale-lock failure) and the profile dir
        # is disposable. A still-alive process that simply never opened CDP
        # is left to the diagnostic, not a retry.
        if attempt + 1 < attempts and proc_exited and _profile_is_disposable(profile_dir):
            continue
        raise CDPError(last_msg)
    raise CDPError(last_msg)



# JS payload: walk the DOM, return JSON array. Single top-level expression
# so CDP Runtime.evaluate(returnByValue=True) can serialise it directly.
_DOM_JS = """(function() {
  var INTERACTIVE = "a[href],button,input,textarea,select,[contenteditable]," + "[tabindex],[role=button],[role=link],[role=checkbox],[role=radio],[role=tab],[role=menuitem],summary,label";
  function nameOf(el) {
    var al = el.getAttribute("aria-label");
    if (al && al.trim()) return al.trim();
    var ph = el.getAttribute("placeholder");
    if (ph && ph.trim()) return ph.trim();
    var tt = el.getAttribute("title");
    if (tt && tt.trim()) return tt.trim();
    var al2 = el.getAttribute("alt");
    if (al2 && al2.trim()) return al2.trim();
    var txt = (el.innerText || el.textContent || "").trim();
    if (txt) return txt.replace(/\\s+/g, " ").slice(0, 200);
    var n = el.getAttribute("name");
    if (n) return n;
    return el.id || "";
  }
  function roleOf(el) { return el.getAttribute("role") || ""; }
  function textOf(el) { var t = (el.innerText || el.textContent || "").trim(); return t ? t.replace(/\\s+/g, " ").slice(0, 200) : ""; }
  function parentTypes(el) { var out = [], p = el; while (p && p !== document.body && out.length < 6) { p = p.parentElement; if (p) out.push(p.tagName.toLowerCase()); } return out.reverse(); }
  function selectorOf(el) { if (el.id) return "#" + el.id; if (el.getAttribute("data-testid")) return "[data-testid=" + JSON.stringify(el.getAttribute("data-testid")) + "]"; if (el.getAttribute("name")) return el.tagName.toLowerCase() + "[name=" + JSON.stringify(el.getAttribute("name")) + "]"; return el.tagName.toLowerCase(); }
  var nodes = document.querySelectorAll(INTERACTIVE);
  var out = [];
  for (var i = 0; i < nodes.length; i++) {
    var el = nodes[i];
    if (!el.isConnected) continue;
    var r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) continue;
    var style = window.getComputedStyle(el);
    if (style.visibility === "hidden" || style.display === "none") continue;
    var cx = r.left + window.scrollX;
    var cy = r.top + window.scrollY;
    out.push({
      tag: el.tagName.toLowerCase(),
      role: roleOf(el),
      type: el.getAttribute("type") || "",
      name: nameOf(el),
      text: textOf(el),
      automation_id: el.id || el.getAttribute("data-testid") || "",
      class_name: el.className && typeof el.className === "string" ? el.className : "",
      selector: selectorOf(el),
      bbox: { x: Math.round(cx), y: Math.round(cy), w: Math.round(r.width), h: Math.round(r.height) },
      center: { x: Math.round(cx + r.width / 2), y: Math.round(cy + r.height / 2) },
      parent_chain: parentTypes(el),
      enabled: !el.disabled && !el.getAttribute("aria-disabled"),
      visible: true,
      focused: el === document.activeElement,
      href: el.getAttribute("href") || "",
      value: el.value !== undefined ? String(el.value).slice(0, 80) : "",
    });
  }
  return out;
})()"""


def _make_element(raw: dict) -> Element:
    bbox = BBox(**raw["bbox"])
    center = Center(**raw["center"])
    return Element(
        backend="cdp",
        control_type=raw.get("control_type") or raw["tag"].title(),
        name=raw.get("name", ""),
        bbox=bbox,
        center=center,
        automation_id=raw.get("automation_id", ""),
        class_name=raw.get("class_name", ""),
        score=1.0,
        interactive=True,
        state=ElementState(
            enabled=raw.get("enabled", True),
            visible=raw.get("visible", True),
            focused=raw.get("focused", False),
        ),
        parent_chain=raw.get("parent_chain", []),
    )


def _js_control_type_map(raw: dict) -> str:
    """Same labels as the JS probe - keeps tests independent of JS."""
    tag = raw.get("tag", "")
    role = raw.get("role", "")
    type_ = (raw.get("type") or "").lower()
    if tag == "a" or role == "link":
        return "Hyperlink"
    if tag in ("button", "summary") or role == "button":
        return "Button"
    if tag == "select":
        return "ComboBox"
    if tag == "textarea":
        return "Edit"
    if tag == "input":
        if type_ in ("submit", "button"):
            return "Button"
        if type_ == "checkbox":
            return "CheckBox"
        if type_ == "radio":
            return "RadioButton"
        return "Edit"
    if role == "checkbox":
        return "CheckBox"
    if role == "radio":
        return "RadioButton"
    if role == "tab":
        return "Tab"
    if role == "menuitem":
        return "MenuItem"
    return raw.get("control_type") or (tag.title() if tag else "Unknown")


def scan_dom(conn, *, filter_url_contains=None, limit: int = 500) -> list:
    raw = conn.evaluate(_DOM_JS)
    if not isinstance(raw, list):
        return []
    out = []
    for r in raw[:limit]:
        r2 = dict(r)
        r2["control_type"] = _js_control_type_map(r)
        out.append(_make_element(r2))
    return out

# ===== Actuation (CDP Input domain) =====


def click_center(conn, element: Element) -> None:
    cx, cy = element.center.x, element.center.y
    common = {"x": float(cx), "y": float(cy), "button": "left", "clickCount": 1, "buttons": 1}
    conn.call("Input.dispatchMouseEvent", {"type": "mousePressed", **common})
    time.sleep(0.02)
    conn.call("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": float(cx), "y": float(cy), "button": "left", "clickCount": 1, "buttons": 0})


def click_xy(conn, x: int, y: int) -> None:
    click_center(conn, Element(backend="cdp", control_type="Point", name="", bbox=BBox(x - 5, y - 5, 10, 10), center=Center(x, y)))


_FOCUS_JS_TMPL = """(function(sel) { try { var el = document.querySelector(sel); } catch (e) { return {ok:false, msg: "bad selector"}; } if (!el) return {ok:false, msg: "no element"}; try { el.focus(); } catch (e) {} if (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable) { try { el.setSelectionRange && el.setSelectionRange(el.value ? el.value.length : 0, el.value ? el.value.length : 0); } catch (e) {} } return {ok:true, tag: el.tagName.toLowerCase()}; })(arguments[0])"""


def focus_element(conn, element: Element) -> bool:
    sel = ""
    if element.automation_id:
        sel = "#" + element.automation_id
    elif element.class_name:
        cls = element.class_name.split()[0]
        sel = "." + cls
    else:
        sel = element.control_type.lower()
    expr = _FOCUS_JS_TMPL.replace("arguments[0]", repr(sel))
    out = conn.evaluate(expr)
    return bool(isinstance(out, dict) and out.get("ok"))


def type_text(conn, text: str, element=None, press_enter: bool = False) -> None:
    if element is not None:
        if not focus_element(conn, element):
            raise CDPError("could not focus element: " + repr(element.name))
        # Best-effort clear of text-like controls
        conn.evaluate("(function(){var e=document.activeElement;if(e&&(e.value!==undefined)){e.value=\"\";}return true;})()")
    conn.call("Input.insertText", {"text": text})
    if press_enter:
        conn.call("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13})
        conn.call("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13})


def scroll(conn, dx: int = 0, dy: int = 200) -> None:
    common = {"x": 200, "y": 200, "button": "middle", "clickCount": 1, "buttons": 0}
    conn.call("Input.dispatchMouseEvent", {"type": "mouseWheel", "deltaX": dx, "deltaY": dy, **common})


def screenshot(conn, out_path: str) -> str:
    data = conn.call("Page.captureScreenshot", {"format": "png"})
    png = base64.b64decode(data["data"])
    p = Path(out_path).absolute()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(png)
    return str(p)
