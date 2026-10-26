"""Windows UIA backend — wraps pywinauto + ctypes for screen / detect."""
from __future__ import annotations

import ctypes
import re
import time
from typing import Any

from PIL import Image, ImageGrab
from pywinauto import Desktop

from openeyes.core.actuator import click_xy
from openeyes.core.dpi import ensure_dpi_awareness
from openeyes.core.schema import BBox, Center, Element, WindowInfo

INTERACTIVE_TYPES = {
    "Button", "Hyperlink", "Edit", "Document", "CheckBox", "RadioButton",
    "ComboBox", "List", "ListItem", "MenuItem", "Menu", "MenuBar",
    "Tab", "TabItem", "Tree", "TreeItem", "DataItem", "Table",
    "SplitButton", "ToggleSwitch", "Slider", "ProgressBar",
    "Image", "Pane", "Window", "Text", "TextBlock",
}

user32 = ctypes.windll.user32


def list_windows() -> list[WindowInfo]:
    import win32gui
    ensure_dpi_awareness()
    out: list[WindowInfo] = []

    def cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd)
        if not title:
            return
        cls = win32gui.GetClassName(hwnd)
        try:
            pid = win32gui.GetWindowThreadProcessId(hwnd)
        except Exception:
            pid = 0
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        out.append(WindowInfo(
            hwnd=hwnd, pid=pid, title=title, class_name=cls,
            x=left, y=top, w=right - left, h=bottom - top,
        ))

    win32gui.EnumWindows(cb, None)
    return out


def capture_screen() -> Image.Image:
    return ImageGrab.grab(all_screens=True)


def capture_window(hwnd: int) -> Image.Image:
    import win32gui
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    return ImageGrab.grab(bbox=(left, top, right, bottom), all_screens=True)


def _call(x):
    """pywinauto's rect.left etc. may be int property or callable — normalize."""
    return int(x() if callable(x) else x)


def _walk(node, depth: int, max_depth: int, out: list[Element],
          ox: int = 0, oy: int = 0, parent_types: list[str] | None = None) -> None:
    if depth > max_depth:
        return
    name = (node.name or "").strip()
    ct = node.control_type or ""
    if (ct in INTERACTIVE_TYPES) or name:
        try:
            left, top, width, height = (_call(node.rectangle.left),
                                        _call(node.rectangle.top),
                                        _call(node.rectangle.width),
                                        _call(node.rectangle.height))
        except Exception:
            return
        out.append(Element(
            backend="uia",
            control_type=ct,
            name=name,
            bbox=BBox(x=left - ox, y=top - oy, w=width, h=height),
            center=Center(x=left - ox + width // 2, y=top - oy + height // 2),
            automation_id=node.automation_id or "",
            class_name=node.class_name or "",
            score=1.0,
            interactive=(ct in INTERACTIVE_TYPES),
            parent_chain=list(parent_types or []),
        ))
    try:
        children = node.children() if callable(node.children) else node.children
    except Exception:
        return
    chain = (parent_types or []) + [ct] if ct else (parent_types or [])
    for c in children:
        _walk(c, depth + 1, max_depth, out, ox, oy, chain)


def detect_elements(hwnd: int, *, restore: bool = False,
                    max_depth: int = 12) -> list[Element]:
    """Enumerate UIA elements under the given hwnd.

    On UWP / Windows Store apps, the accessibility tree is empty when the
    window is minimized. Pass ``restore=True`` to call ShowWindow(SW_RESTORE)
    first.
    """
    ensure_dpi_awareness()
    if restore:
        try:
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            time.sleep(0.25)
        except Exception:
            pass
    d = Desktop(backend="uia")
    w = d.window(handle=hwnd)
    out: list[Element] = []
    _walk(w.element_info, 0, max_depth, out)
    return out


def find_by_automation_id(hwnd: int, automation_id: str, *,
                          max_depth: int = 12) -> Element | None:
    """Return the first element whose automation_id matches.

    The automation id is the stable anchor of a control (e.g. Any VPN's
    keepalive button is ``KeepFreeAliveButton``), while the visible name
    may change every second.
    """
    for e in detect_elements(hwnd, max_depth=max_depth):
        if e.automation_id == automation_id:
            return e
    return None


def _resolve_wrapper(hwnd: int, *, automation_id: str | None = None,
                     name_regex: str | None = None,
                     name_contains: str | None = None,
                     control_type: str | None = None):
    """Resolve a live pywinauto wrapper (needed for the Invoke pattern)."""
    kwargs: dict[str, Any] = {}
    if automation_id:
        kwargs["auto_id"] = automation_id
    if name_regex:
        kwargs["title_re"] = name_regex
    elif name_contains:
        kwargs["title_re"] = re.escape(name_contains)
    if control_type:
        kwargs["control_type"] = control_type
    if not kwargs:
        raise ValueError("need automation_id / name_regex / name_contains")
    d = Desktop(backend="uia")
    return d.window(handle=hwnd).child_window(**kwargs).wrapper_object()


def click_element(hwnd: int, *, automation_id: str | None = None,
                  name_regex: str | None = None,
                  name_contains: str | None = None,
                  control_type: str | None = None,
                  method: str = "auto", dry_run: bool = True,
                  press_ms: int = 30, settle_ms: int = 80) -> dict:
    """Click an element, preferring the UIA Invoke pattern over pixel clicks.

    ``method``:

    * ``"auto"``   — try Invoke first, fall back to a pixel click.
    * ``"invoke"`` — Invoke pattern only.
    * ``"pixel"``  — mouse click at the element centre only.

    The Invoke pattern needs no pixel maths, no window focus and never
    touches the user's cursor, which makes it both more accurate and less
    intrusive than moving the mouse onto the element.

    Returns a report dict: ``ok``, ``method``, ``name``, ``automation_id``,
    ``control_type``, ``center``, ``landed`` (pixel clicks: did the cursor
    arrive?), ``error``.
    """
    report: dict[str, Any] = {
        "ok": False, "method": None, "name": "", "automation_id": "",
        "control_type": "", "center": None, "landed": None, "error": None,
    }
    try:
        wrapper = _resolve_wrapper(hwnd, automation_id=automation_id,
                                   name_regex=name_regex,
                                   name_contains=name_contains,
                                   control_type=control_type)
    except Exception as e:
        report["error"] = f"resolve failed: {e}"
        return report

    info = wrapper.element_info
    report["name"] = info.name or ""
    report["automation_id"] = info.automation_id or ""
    report["control_type"] = info.control_type or ""
    try:
        r = info.rectangle
        cx, cy = (r.left + r.right) // 2, (r.top + r.bottom) // 2
        report["center"] = (cx, cy)
    except Exception:
        cx = cy = None

    if dry_run:
        report["ok"] = True
        report["method"] = "dry-run"
        return report

    if method in ("auto", "invoke"):
        try:
            wrapper.invoke()
            report["ok"] = True
            report["method"] = "invoke"
            return report
        except Exception as e:
            report["error"] = f"invoke failed: {e}"
            if method == "invoke":
                return report

    if cx is None:
        report["error"] = (report["error"] or "") + " | no rect for pixel click"
        return report
    try:
        from openeyes.actuators.win32 import cursor_pos, focus_window
        focus_window(hwnd)
        time.sleep(0.1)
        click_xy(cx, cy, press_ms=press_ms, settle_ms=settle_ms)
        pos = cursor_pos()
        report["landed"] = bool(pos is not None
                                and abs(pos[0] - cx) <= 1
                                and abs(pos[1] - cy) <= 1)
        report["ok"] = True
        report["method"] = "pixel"
        return report
    except Exception as e:
        report["error"] = (report["error"] or "") + f" | pixel click failed: {e}"
        return report