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


#: How deep the accessibility tree is walked by default.
#:
#: 12 is enough for ordinary Win32 / UWP chrome, but a WebView2 surface
#: (Tauri, Electron, WebView2 apps such as Yuanbao) buries the DOM far
#: deeper: its send button only appears around depth 20. A default that
#: cannot see the control makes every id-anchored lookup miss, so the
#: default is deep enough to reach a web surface.
DEFAULT_MAX_DEPTH = 30


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
                    max_depth: int = DEFAULT_MAX_DEPTH) -> list[Element]:
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
                          max_depth: int = DEFAULT_MAX_DEPTH) -> Element | None:
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


#: The delivery rungs, cheapest first. The ladder walks them in order.
LADDER = ("invoke", "message", "pixel", "foreground")


def _ladder_for(method: str) -> tuple[str, ...]:
    """Which rungs a method may use.

    ``auto`` walks the ladder. The other names pick one rung: a caller
    that asks for a specific delivery gets that delivery or a failure -
    never a different one behind its back.
    """
    name = (method or "auto").lower()
    if name == "auto":
        return ("invoke", "message", "pixel")
    if name in LADDER:
        return (name,)
    return ("invoke", "message", "pixel")


def _post_click(wrapper: Any, control_type: str) -> bool:
    """Ask the control to act through a window message, not the pointer.

    Only a control that owns a window can be asked this way: the message
    goes to its own HWND and the application handles it in its own
    message loop, so the pointer never moves and the window need not be
    in the foreground.
    """
    if (control_type or "").lower() != "button":
        return False
    try:
        handle = int(getattr(wrapper.element_info, "handle", 0) or 0)
    except Exception:
        return False
    if not handle:
        return False
    try:
        import win32con
        import win32gui
        # Posted, not sent: a hung application must not hang us.
        win32gui.PostMessage(handle, win32con.BM_CLICK, 0, 0)
    except Exception:
        return False
    return True


def _deliver(wrapper: Any, hwnd: int, *, method: str, budget: Any,
             cx: int | None, cy: int | None, control_type: str,
             press_ms: int, settle_ms: int, report: dict[str, Any]) -> dict[str, Any]:
    """Walk the delivery ladder and report what happened.

    The rungs that reach for the user's pointer are paid for from the
    disturbance budget, because a physical click may take the window to
    the front. An explicit request (``method="pixel"``) is an
    authorisation in itself: it is honoured, and it is still counted.
    """
    from openeyes.agent.errors import (
        BACKGROUND_UNAVAILABLE,
        BUDGET_EXCEEDED,
        NO_SEMANTIC_PATH,
    )

    explicit = method in ("pixel", "foreground")
    for rung in _ladder_for(method):
        if rung == "invoke":
            try:
                wrapper.invoke()
            except Exception as exc:
                report["error"] = f"invoke failed: {exc}"
                continue
            report["ok"] = True
            report["method"] = "invoke"
            return report

        if rung == "message":
            if _post_click(wrapper, control_type):
                report["ok"] = True
                report["method"] = "message"
                return report
            report["error"] = ((report["error"] or "")
                                + " | no window message for this control")
            continue

        if rung == "pixel":
            if not explicit and budget is not None and not budget.spend():
                report["error_code"] = (BUDGET_EXCEEDED if budget.exhausted
                                        else BACKGROUND_UNAVAILABLE)
                report["error"] = ((report["error"] or "no semantic path")
                                   + " | the disturbance budget does not allow "
                                     "a physical click")
                return report
            if cx is None:
                report["error"] = ((report["error"] or "")
                                   + " | no rect for pixel click")
                continue
            try:
                from openeyes.actuators.win32 import cursor_pos
                click_xy(cx, cy, press_ms=press_ms, settle_ms=settle_ms)
                pos = cursor_pos()
                report["landed"] = bool(pos is not None
                                        and abs(pos[0] - cx) <= 1
                                        and abs(pos[1] - cy) <= 1)
                report["ok"] = True
                report["method"] = "pixel"
                return report
            except Exception as exc:
                report["error"] = ((report["error"] or "")
                                   + f" | pixel click failed: {exc}")
                continue

        if rung == "foreground":
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
                report["method"] = "foreground"
                return report
            except Exception as exc:
                report["error"] = ((report["error"] or "")
                                   + f" | foreground click failed: {exc}")
                continue

    report["error_code"] = NO_SEMANTIC_PATH
    return report


def click_element(hwnd: int, *, automation_id: str | None = None,
                  name_regex: str | None = None,
                  name_contains: str | None = None,
                  control_type: str | None = None,
                  method: str = "auto", dry_run: bool = True,
                  press_ms: int = 30, settle_ms: int = 80,
                  budget: Any | None = None) -> dict:
    """Click an element, preferring the cheapest delivery path.

    The ladder, cheapest first:

    * ``invoke``   — the UIA Invoke pattern. No pixel maths, no window
      focus, and the user's cursor is never touched.
    * ``message``  — a window message to the control itself. The pointer
      stays where it is and the window need not be in the foreground.
    * ``pixel``    — a synthesized pointer click at the element centre.
    * ``foreground`` — take the window to the front, then click.

    ``method`` picks how far up the ladder the caller is willing to go:

    * ``"auto"``      — walk ``invoke -> message -> pixel``.
    * ``"invoke"`` / ``"message"`` / ``"pixel"`` / ``"foreground"`` — one
      rung only: that delivery, or a failure. Never a different one.

    ``budget`` is an ``openeyes.agent.profile.FocusBudget``. When one is
    given, the rungs that reach for the user's pointer are paid for from
    it: with no budget left the action fails with a structured error
    instead of taking the pointer. Without a budget nothing is gated -
    that is the caller's explicit choice.

    Returns a report dict: ``ok``, ``method``, ``name``, ``automation_id``,
    ``control_type``, ``center``, ``landed`` (pixel clicks: did the cursor
    arrive?), ``physical_input`` (did the user's pointer get driven?),
    ``cursor_moved``, ``focus_stolen`` (did the focus come to the target?),
    ``error``, ``error_code`` (a structured code, when one applies).

    The last three are what the click cost the human sharing the desktop.
    A delivered action is not a successful one, and a semantic action is
    not a physical one: the report keeps the two apart.
    """
    report: dict[str, Any] = {
        "ok": False, "method": None, "name": "", "automation_id": "",
        "control_type": "", "center": None, "landed": None,
        "physical_input": False, "cursor_moved": False,
        "focus_stolen": False, "error": None, "error_code": None,
    }
    from openeyes.core.windows import foreground_window

    foreground_before = foreground_window()

    def finish(done: dict[str, Any]) -> dict[str, Any]:
        """Close the report with what the action cost the user.

        The focus was stolen when it came onto the target from somewhere
        else; the pointer was driven only by a pixel click that actually
        landed. An Invoke never touches either.
        """
        done["focus_stolen"] = bool(hwnd) and foreground_before != hwnd \
            and foreground_window() == hwnd
        done["physical_input"] = bool(done.get("method") in ("pixel", "foreground")
                                      and done.get("landed"))
        done["cursor_moved"] = done["physical_input"]
        return done

    try:
        wrapper = _resolve_wrapper(hwnd, automation_id=automation_id,
                                   name_regex=name_regex,
                                   name_contains=name_contains,
                                   control_type=control_type)
    except Exception as e:
        report["error"] = f"resolve failed: {e}"
        return finish(report)

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
        return finish(report)

    return finish(_deliver(wrapper, hwnd, method=method, budget=budget,
                           cx=cx, cy=cy, control_type=report["control_type"],
                           press_ms=press_ms, settle_ms=settle_ms,
                           report=report))