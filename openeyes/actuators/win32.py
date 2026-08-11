"""Win32 mouse / keyboard input via pywin32."""
from __future__ import annotations
import time
import ctypes
import win32api
import win32con
import win32gui
from pywinauto import Desktop

user32 = ctypes.windll.user32

VK = {
    "enter": win32con.VK_RETURN, "return": win32con.VK_RETURN,
    "esc": win32con.VK_ESCAPE, "escape": win32con.VK_ESCAPE,
    "tab": win32con.VK_TAB, "space": win32con.VK_SPACE,
    "backspace": win32con.VK_BACK, "delete": win32con.VK_DELETE,
    "f1": win32con.VK_F1, "f2": win32con.VK_F2, "f3": win32con.VK_F3,
    "f4": win32con.VK_F4, "f5": win32con.VK_F5, "f6": win32con.VK_F6,
    "f7": win32con.VK_F7, "f8": win32con.VK_F8, "f9": win32con.VK_F9,
    "f10": win32con.VK_F10, "f11": win32con.VK_F11, "f12": win32con.VK_F12,
    "ctrl": win32con.VK_CONTROL, "alt": win32con.VK_MENU, "shift": win32con.VK_SHIFT,
    "win": win32con.VK_LWIN,
}


def _move(x: int, y: int) -> None:
    win32api.SetCursorPos((x, y))


def click_xy(x: int, y: int, button: str = "left", double: bool = False) -> None:
    """Click at absolute screen coords. Uses mouse_event for synthesized input."""
    _move(x, y)
    time.sleep(0.02)
    bmap = {
        "left": win32con.MOUSEEVENTF_LEFTDOWN,
        "right": win32con.MOUSEEVENTF_RIGHTDOWN,
        "middle": win32con.MOUSEEVENTF_MIDDLEDOWN,
    }
    umap = {
        "left": win32con.MOUSEEVENTF_LEFTUP,
        "right": win32con.MOUSEEVENTF_RIGHTUP,
        "middle": win32con.MOUSEEVENTF_MIDDLEUP,
    }
    n = 2 if double else 1
    for _ in range(n):
        win32api.mouse_event(bmap[button], 0, 0, 0, 0)
        win32api.mouse_event(umap[button], 0, 0, 0, 0)
        time.sleep(0.02)


def send_hotkey(*keys: str) -> None:
    """Press a chord. e.g. send_hotkey('ctrl', 'a')."""
    mods = [k for k in keys if k in ("ctrl", "alt", "shift", "win")]
    rest = [k for k in keys if k not in mods]
    down_mods = []
    try:
        for m in mods:
            v = VK[m]
            user32.keybd_event(v, 0, 0, 0)
            down_mods.append(v)
        for k in rest:
            vk = VK.get(k.lower())
            if vk is None and len(k) == 1:
                vk = ord(k.upper())
            user32.keybd_event(vk, 0, 0, 0)
            user32.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP, 0)
    finally:
        for v in reversed(down_mods):
            user32.keybd_event(v, 0, win32con.KEYEVENTF_KEYUP, 0)


def type_text(text: str, interval: float = 0.0) -> None:
    """Type a literal string. Uses keybd_event for ASCII chars only."""
    for ch in text:
        vk = ord(ch.upper())
        user32.keybd_event(vk, 0, 0, 0)
        user32.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP, 0)
        if interval:
            time.sleep(interval)


def drag(from_xy: tuple[int, int], to_xy: tuple[int, int],
         duration_ms: int = 200) -> None:
    """Click-drag from one point to another."""
    x0, y0 = from_xy
    x1, y1 = to_xy
    _move(x0, y0)
    time.sleep(0.02)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    steps = max(10, duration_ms // 20)
    for i in range(1, steps + 1):
        x = x0 + (x1 - x0) * i // steps
        y = y0 + (y1 - y0) * i // steps
        _move(x, y)
        time.sleep(duration_ms / 1000 / steps)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)


def scroll(dx: int, dy: int, at_xy: tuple[int, int] | None = None) -> None:
    """Scroll wheel at current or specified position."""
    if at_xy is not None:
        _move(*at_xy)
        time.sleep(0.02)
    win32api.mouse_event(win32con.MOUSEEVENTF_WHEEL, 0, 0, -dy * 120, 0)
    if dx:
        win32api.mouse_event(win32con.MOUSEEVENTF_HWHEEL, 0, 0, dx * 120, 0)


def focus_window(hwnd: int) -> bool:
    """Bring the window to foreground (best-effort). Returns True on success.

    UWP / Windows Store apps are tricky: ``set_focus()`` often silently fails
    because the actual content lives inside a child CoreWindow of an
    ApplicationFrameWindow. If the first attempt does not bring the window
    to the foreground (verified via GetForegroundWindow), we click the title
    bar as a fallback — that usually forces Windows to give us focus.
    """
    import win32gui
    d = Desktop(backend="uia")
    w = d.window(handle=hwnd)
    try:
        w.set_focus()
    except Exception:
        pass
    try:
        fg = win32gui.GetForegroundWindow()
        if fg == hwnd:
            return True
    except Exception:
        return False
    # Fallback: click the title bar (top-center, 20px down from window top).
    try:
        l, t, r, _ = win32gui.GetWindowRect(hwnd)
        title_x = (l + r) // 2
        title_y = t + 20
        click_xy(title_x, title_y)
        time.sleep(0.2)
        fg = win32gui.GetForegroundWindow()
        return fg == hwnd
    except Exception:
        return False