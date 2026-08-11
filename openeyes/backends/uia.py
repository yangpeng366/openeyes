"""Windows UIA backend — wraps pywinauto + ctypes for screen / detect."""
from __future__ import annotations
import ctypes
import time
from typing import Any
from PIL import Image, ImageGrab
from pywinauto import Desktop

from openeyes.core.schema import BBox, Center, Element, ElementState, WindowInfo

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
        l, top, r, b = win32gui.GetWindowRect(hwnd)
        out.append(WindowInfo(
            hwnd=hwnd, pid=pid, title=title, class_name=cls,
            x=l, y=top, w=r - l, h=b - top,
        ))

    win32gui.EnumWindows(cb, None)
    return out


def capture_screen() -> Image.Image:
    return ImageGrab.grab(all_screens=True)


def capture_window(hwnd: int) -> Image.Image:
    import win32gui
    l, t, r, b = win32gui.GetWindowRect(hwnd)
    return ImageGrab.grab(bbox=(l, t, r, b), all_screens=True)


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
            l, t, w, h = (_call(node.rectangle.left),
                         _call(node.rectangle.top),
                         _call(node.rectangle.width),
                         _call(node.rectangle.height))
        except Exception:
            return
        out.append(Element(
            backend="uia",
            control_type=ct,
            name=name,
            bbox=BBox(x=l - ox, y=t - oy, w=w, h=h),
            center=Center(x=l - ox + w // 2, y=t - oy + h // 2),
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