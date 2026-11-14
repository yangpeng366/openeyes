"""Win32 mouse / keyboard input via pywin32."""
from __future__ import annotations

import contextlib
import ctypes
import time
from ctypes import wintypes

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


# SendInput keyboard packets. Unicode packets (wVk=0, wScan=code unit) arrive
# as WM_CHAR, which is what lets typing skip the IME entirely.
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = (
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    )


class _INPUT(ctypes.Structure):
    class _Union(ctypes.Union):
        _fields_ = (("ki", _KEYBDINPUT),)

    _anonymous_ = ("u",)
    _fields_ = (("type", wintypes.DWORD), ("u", _Union))


user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int)
user32.SendInput.restype = wintypes.UINT


def _move(x: int, y: int) -> None:
    win32api.SetCursorPos((x, y))


def cursor_pos() -> tuple[int, int] | None:
    """Current cursor position, or None if it cannot be read."""
    try:
        return tuple(win32api.GetCursorPos())  # type: ignore[return-value]
    except Exception:
        return None


def move_to(x: int, y: int, settle_ms: int = 60, steps: int = 2) -> bool:
    """Move the cursor to (x, y) and wait for the target to see the motion.

    A single ``SetCursorPos`` immediately followed by a click can be
    delivered at the *previous* cursor position: the target window has not
    processed WM_MOUSEMOVE yet, so the synthesised button press lands where
    the cursor used to be. Moving in a couple of steps with a settle pause
    removes that race.

    Returns True when the cursor ended up exactly on (x, y).
    """
    steps = max(1, int(steps))
    sx, sy = cursor_pos() or (x, y)
    for i in range(1, steps + 1):
        ix = int(sx + (x - sx) * i / steps)
        iy = int(sy + (y - sy) * i / steps)
        _move(ix, iy)
        if settle_ms > 0:
            time.sleep(settle_ms / 1000.0 / steps)
    _move(x, y)
    if settle_ms > 0:
        time.sleep(settle_ms / 1000.0)
    pos = cursor_pos()
    return pos is not None and abs(pos[0] - x) <= 1 and abs(pos[1] - y) <= 1


def click_xy(x: int, y: int, button: str = "left", double: bool = False,
             press_ms: int = 30, settle_ms: int = 60) -> None:
    """Click at absolute screen coords. Uses mouse_event for synthesized input.

    ``press_ms`` holds the button down for a moment before releasing it —
    XAML/UWP controls can ignore a zero-duration click. ``settle_ms`` is the
    pause after the cursor has been moved onto the target, so the window has
    processed the motion before the press arrives.
    """
    move_to(x, y, settle_ms=settle_ms)
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
        if press_ms > 0:
            time.sleep(press_ms / 1000.0)
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


def _vk_for_char(ch: str) -> tuple[int, bool] | None:
    """Virtual key and Shift flag for one character, or None.

    ``ord(ch)`` is not a virtual-key code: "," would arrive as
    VK_SNAPSHOT and "@" as a key that does not exist. ``VkKeyScanW`` asks
    the active layout the question we actually mean.
    """
    scan = user32.VkKeyScanW(ctypes.c_wchar(ch))
    if scan == -1 or scan == 0xFFFF:
        return None
    vk = scan & 0xFF
    shift = bool(scan & 0x0100)
    if scan & 0x0600:  # Ctrl or Alt needed: not expressible here
        return None
    return vk, shift


def _utf16_units(ch: str) -> list[int]:
    """UTF-16 code units for one character (astral chars become two)."""
    raw = ch.encode("utf-16-le")
    return [int.from_bytes(raw[i:i + 2], "little") for i in range(0, len(raw), 2)]


def _send_unicode_text(text: str, interval: float) -> int:
    """Inject text as WM_CHAR-level Unicode packets; returns chars inserted.

    ``SendInput`` reports how many events it inserted; anything short of
    two per code unit means the target refused synthetic input (UIPI), and
    the caller should fall back to the keyboard-layout path.
    """
    inserted = 0
    for ch in text:
        units = _utf16_units(ch)
        events = (_INPUT * (2 * len(units)))()
        for i, unit in enumerate(units):
            down, up = events[2 * i], events[2 * i + 1]
            down.type = INPUT_KEYBOARD
            up.type = INPUT_KEYBOARD
            down.ki.wScan = unit
            up.ki.wScan = unit
            down.ki.dwFlags = KEYEVENTF_UNICODE
            up.ki.dwFlags = KEYEVENTF_UNICODE | KEYEVENTF_KEYUP
        sent = user32.SendInput(len(events), events, ctypes.sizeof(_INPUT))
        if sent != len(events):
            break
        inserted += 1
        if interval:
            time.sleep(interval)
    return inserted


def _type_by_layout(text: str, interval: float) -> None:
    """Type through the active keyboard layout (the pre-Unicode path).

    Shift is pressed when the character needs it, so "a@b.com" arrives
    as written. A character the layout cannot produce raises ValueError -
    silently typing the wrong key is worse than failing.
    """
    for ch in text:
        pair = _vk_for_char(ch)
        if pair is None:
            raise ValueError(
                f"cannot type {ch!r} on the active keyboard layout"
            )
        vk, shift = pair
        if shift:
            user32.keybd_event(win32con.VK_SHIFT, 0, 0, 0)
        user32.keybd_event(vk, 0, 0, 0)
        user32.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP, 0)
        if shift:
            user32.keybd_event(win32con.VK_SHIFT, 0, win32con.KEYEVENTF_KEYUP, 0)
        if interval:
            time.sleep(interval)


# IME conversion flags / notifications (imm.h).
IME_CMODE_ALPHANUMERIC = 0x0000
IME_CMODE_NATIVE = 0x0001
IME_CMODE_KATAKANA = 0x0002
IME_CMODE_FULLSHAPE = 0x0008
NI_CLOSECANDIDATE = 0x0004


def _imm32():
    """imm32 with 64-bit-safe prototypes (HIMC is a pointer, not an int)."""
    imm = ctypes.WinDLL("imm32")
    imm.ImmGetContext.argtypes = (wintypes.HWND,)
    imm.ImmGetContext.restype = wintypes.HANDLE
    imm.ImmReleaseContext.argtypes = (wintypes.HWND, wintypes.HANDLE)
    imm.ImmReleaseContext.restype = wintypes.BOOL
    imm.ImmGetConversionStatus.argtypes = (
        wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
    )
    imm.ImmGetConversionStatus.restype = wintypes.BOOL
    imm.ImmSetConversionStatus.argtypes = (
        wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
    )
    imm.ImmSetConversionStatus.restype = wintypes.BOOL
    imm.ImmGetOpenStatus.argtypes = (wintypes.HANDLE,)
    imm.ImmGetOpenStatus.restype = wintypes.BOOL
    imm.ImmSetOpenStatus.argtypes = (wintypes.HANDLE, wintypes.BOOL)
    imm.ImmSetOpenStatus.restype = wintypes.BOOL
    imm.ImmNotifyIME.argtypes = (
        wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
    )
    imm.ImmNotifyIME.restype = wintypes.BOOL
    return imm


@contextlib.contextmanager
def _ascii_ime_guard():
    """Force the foreground window's IME into English for the block.

    Only the layout fallback needs this: Unicode packets never reach an
    IME, but per-key events do, and a Chinese IME in native mode swallows
    ``https://`` into a pinyin composition. The previous conversion mode
    and open state are restored even when typing raises.
    """
    try:
        imm = _imm32()
    except OSError:  # pragma: no cover - imm32 is present on Windows
        yield
        return
    hwnd = 0
    himc = None
    previous = None
    try:
        hwnd = win32gui.GetForegroundWindow()
        himc = imm.ImmGetContext(hwnd)
        if himc:
            conv = wintypes.DWORD()
            sent = wintypes.DWORD()
            if imm.ImmGetConversionStatus(
                    himc, ctypes.byref(conv), ctypes.byref(sent)):
                previous = (
                    conv.value, sent.value,
                    bool(imm.ImmGetOpenStatus(himc)),
                )
                if conv.value & (IME_CMODE_NATIVE | IME_CMODE_KATAKANA
                                 | IME_CMODE_FULLSHAPE):
                    imm.ImmSetConversionStatus(
                        himc, IME_CMODE_ALPHANUMERIC, sent.value)
                    imm.ImmNotifyIME(himc, NI_CLOSECANDIDATE, 0, 0)
        yield
    finally:
        if himc:
            try:
                if previous is not None:
                    conv, sent, was_open = previous
                    imm.ImmSetConversionStatus(himc, conv, sent)
                    imm.ImmSetOpenStatus(himc, was_open)
            finally:
                imm.ImmReleaseContext(hwnd, himc)


def type_text(text: str, interval: float = 0.0) -> None:
    """Type a literal string, independent of the active input method.

    Characters go in as ``KEYEVENTF_UNICODE`` packets, so a Chinese IME
    left in native mode cannot capture ASCII into a pinyin composition.
    That capture is what used to empty address bars: ``Ctrl+L`` followed
    by ``eyes type`` produced no text while the IME showed candidates,
    and only switching the IME to English by hand made URLs arrive.

    Non-ASCII text works too - the packets carry code points directly -
    so the old "clipboard paste for CJK" workaround is no longer needed.
    """
    if not text:
        return
    if _send_unicode_text(text, interval) == len(text):
        return
    # Fallback: a target that refuses synthetic input still gets the old
    # layout path, with the IME pinned to English for the duration.
    with _ascii_ime_guard():
        _type_by_layout(text, interval)


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


# Keep in sync with openeyes.core.focus.ERROR_FOCUS_OCCLUDED. The actuator
# owns the refusal but the core layer owns the public error code.
FOCUS_OCCLUDED = "FOCUS_OCCLUDED"


class FocusRefused(RuntimeError):
    """A structured refusal from :func:`_focus_window_impl`.

    ``focus_error_code`` is read (duck-typed) by ``openeyes.core.focus`` so
    that module never has to import this pywin32-backed one at import time.
    The public :func:`focus_window` turns it into a plain ``False`` to keep
    the bool contract every other caller relies on.
    """

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.focus_error_code = code


def _point_belongs_to(point_hwnd: int, target_hwnd: int) -> bool:
    """Whether the window owning a screen point is the target or its child.

    ``WindowFromPoint`` returns the deepest window at the coordinates, so a
    point on the target's title bar can legitimately be a child window. A hit
    belongs to the target when it is the target itself, or when its root
    ancestor (GA_ROOT) is the target. Anything else covers the point and the
    click must not be delivered there.
    """
    if not point_hwnd:
        return False
    point_hwnd = int(point_hwnd)
    target_hwnd = int(target_hwnd)
    if point_hwnd == target_hwnd:
        return True
    try:
        return int(win32gui.GetAncestor(point_hwnd, win32con.GA_ROOT)) == target_hwnd
    except Exception:
        return False


def _focus_window_impl(hwnd: int) -> bool:
    """Bring the window to foreground; raise FocusRefused on an unsafe point.

    UWP / Windows Store apps are tricky: ``set_focus()`` often silently fails
    because the actual content lives inside a child CoreWindow of an
    ApplicationFrameWindow. If the first attempt does not bring the window
    to the foreground (verified via GetForegroundWindow), we click the title
    bar as a fallback - but only when that point actually belongs to the
    target. When another window covers the title bar, the point belongs to
    that occluder; clicking it would hit someone else's content area, so we
    refuse instead of clicking.
    """
    d = Desktop(backend="uia")
    w = d.window(handle=hwnd)
    with contextlib.suppress(Exception):
        w.set_focus()
    try:
        fg = win32gui.GetForegroundWindow()
        if fg == hwnd:
            return True
    except Exception:
        return False
    # Fallback: click the title bar (top-center, 20px down from window top),
    # but guard the point first with WindowFromPoint.
    try:
        left, top, right, _ = win32gui.GetWindowRect(hwnd)
    except Exception:
        return False
    title_x = (left + right) // 2
    title_y = top + 20
    try:
        hit = win32gui.WindowFromPoint((title_x, title_y))
    except Exception:
        return False
    if not _point_belongs_to(hit, hwnd):
        raise FocusRefused(
            FOCUS_OCCLUDED,
            f"title-bar point ({title_x},{title_y}) belongs to "
            f"hwnd={int(hit or 0)}, not target hwnd={hwnd}",
        )
    try:
        click_xy(title_x, title_y)
        time.sleep(0.2)
        fg = win32gui.GetForegroundWindow()
        return fg == hwnd
    except Exception:
        return False


def focus_window(hwnd: int) -> bool:
    """Bring the window to foreground (best-effort). Returns True on success.

    The plain bool contract is preserved for every caller (``eyes click
    --focus-titlebar``, the uia click ladder, ...). When the title-bar
    fallback point is occluded by another window, the structured
    :class:`FocusRefused` raised by :func:`_focus_window_impl` is turned into
    ``False`` here; the core focus layer calls ``_focus_window_impl`` directly
    so it can report that case under its own ``FOCUS_OCCLUDED`` code.
    """
    try:
        return _focus_window_impl(hwnd)
    except FocusRefused:
        return False
