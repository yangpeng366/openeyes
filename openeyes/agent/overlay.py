"""The agent's own cursor: drawn, never driven.

A mouse cluster is only legible if you can tell who is moving. The
system pointer can only be one, so the agents draw their own: a small
overlay cursor per agent, coloured by identity, floating over the
desktop.

Two rules make this safe to put on somebody's screen:

* **it never drives the pointer.** The overlay is a picture. It does
  not move the mouse, it does not click, it does not own the input.
* **it never takes the foreground.** The window is layered,
  click-through and non-activating, so the user's own window keeps the
  focus while an agent's cursor is drawn on top of it.

The drawing itself is a pure function - an image in, an image out - so
the shape can be checked without a screen, and only the small window
plumbing needs a desktop.
"""
from __future__ import annotations

import contextlib
import ctypes
import sys
from ctypes import wintypes
from dataclasses import dataclass

from PIL import Image, ImageDraw

from openeyes.agent.profile import AgentProfile

#: The size of the drawn arrow, in pixels.
CURSOR_SIZE = 22

#: The colour of the outline, so the arrow reads on any background.
OUTLINE = (255, 255, 255, 235)

#: The classic pointer shape, in a 22x22 box.
_ARROW = ((0, 0), (0, 16), (4.5, 12.5), (7, 19), (10, 18), (7.5, 11.5),
          (12, 11.5))

#: The height of the label badge.
LABEL_HEIGHT = 16


def _hex_to_rgba(color: str) -> tuple[int, int, int, int]:
    text = color.strip().lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    if len(text) != 6:
        return (230, 25, 75, 255)
    try:
        red, green, blue = (int(text[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return (230, 25, 75, 255)
    return (red, green, blue, 255)


def _arrow_points(size: int) -> list[tuple[float, float]]:
    scale = size / 22.0
    return [(x * scale, y * scale) for x, y in _ARROW]


def label_width(label: str) -> int:
    """How wide the label badge is, in pixels."""
    if not label:
        return 0
    return 8 + 7 * len(label)


def render_cursor(size: int = CURSOR_SIZE, color: str = "#e6194b",
                  label: str = "") -> Image.Image:
    """Draw the agent's cursor into a fresh image.

    The arrow carries the agent's colour, so two agents are told apart
    at a glance; the label names the one that is busy.
    """
    width = size + label_width(label)
    height = max(size, LABEL_HEIGHT + 2)
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    points = _arrow_points(size)
    shadow = [(x + 1, y + 1) for x, y in points]
    draw.polygon(shadow, fill=(0, 0, 0, 90))
    draw.polygon(points, fill=_hex_to_rgba(color), outline=OUTLINE)

    if label:
        badge_x = size - 2
        badge_y = size - LABEL_HEIGHT - 2
        badge_w = label_width(label)
        draw.rounded_rectangle((badge_x, badge_y, badge_x + badge_w,
                                badge_y + LABEL_HEIGHT),
                               radius=4, fill=_hex_to_rgba(color),
                               outline=OUTLINE)
        draw.text((badge_x + 4, badge_y + 2), label, fill=(255, 255, 255, 255))
    return image


@dataclass
class OverlayState:
    """Where an agent's cursor is drawn, and whether it is showing."""

    x: int = 0
    y: int = 0
    visible: bool = False
    agent_id: str = "default"
    label: str = ""
    color: str = ""


# --------------------------------------------------------------- the plumbing
class _BitmapInfoHeader(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class _RgbQuad(ctypes.Structure):
    _fields_ = [
        ("rgbBlue", ctypes.c_ubyte),
        ("rgbGreen", ctypes.c_ubyte),
        ("rgbRed", ctypes.c_ubyte),
        ("rgbReserved", ctypes.c_ubyte),
    ]


class _BitmapInfo(ctypes.Structure):
    _fields_ = [
        ("bmiHeader", _BitmapInfoHeader),
        ("bmiColors", _RgbQuad * 3),
    ]


def _dib_from_image(image: Image.Image):
    """Build a 32-bit top-down DIB holding the image, and a DC for it.

    Returns ``(hbitmap, hdc)``: the caller must delete both when it is
    done with them.
    """
    gdi32 = ctypes.windll.gdi32
    gdi32.CreateDIBSection.restype = ctypes.c_void_p
    gdi32.CreateDIBSection.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(_BitmapInfo), wintypes.UINT,
        ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, wintypes.DWORD,
    ]
    gdi32.SelectObject.restype = ctypes.c_void_p
    gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
    gdi32.DeleteDC.argtypes = [ctypes.c_void_p]
    gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
    gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]

    width, height = image.size
    info = _BitmapInfo()
    info.bmiHeader.biSize = ctypes.sizeof(_BitmapInfoHeader)
    info.bmiHeader.biWidth = width
    info.bmiHeader.biHeight = -height          # top-down
    info.bmiHeader.biPlanes = 1
    info.bmiHeader.biBitCount = 32
    info.bmiHeader.biCompression = 0           # BI_RGB

    bits = ctypes.c_void_p()
    hbitmap = gdi32.CreateDIBSection(None, ctypes.byref(info), 0,
                                     ctypes.byref(bits), None, 0)
    if not hbitmap:
        raise OSError("could not create the overlay bitmap")

    payload = image.tobytes("raw", "BGRA")
    ctypes.memmove(bits, payload, len(payload))

    hdc = gdi32.CreateCompatibleDC(None)
    if not hdc:
        gdi32.DeleteObject(hbitmap)
        raise OSError("could not create the overlay device context")
    gdi32.SelectObject(hdc, hbitmap)
    return hbitmap, hdc


class CursorOverlay:
    """A picture of an agent's cursor, floating over the desktop.

    The window is layered, click-through and non-activating: the user
    clicks straight through it and their foreground window never
    changes. It draws; it never drives.
    """

    def __init__(self, agent_id: str = "default", label: str = "",
                 color: str = "", size: int = CURSOR_SIZE) -> None:
        profile = AgentProfile(agent_id=agent_id, label=label, color=color)
        self.agent_id = profile.agent_id
        self.label = profile.label
        self.color = profile.color
        self.size = size
        self.state = OverlayState(agent_id=profile.agent_id,
                                  label=profile.label, color=profile.color)
        self._hwnd: int | None = None
        self._class_name = f"OpenEyesCursor_{profile.agent_id}"

    # ------------------------------------------------------------- the image
    def render(self) -> Image.Image:
        """The image this overlay draws."""
        return render_cursor(self.size, self.color, self.label)

    # ------------------------------------------------------------- the window
    @property
    def supported(self) -> bool:
        """Can this machine show an overlay?"""
        return sys.platform.startswith("win")

    def _ensure_window(self) -> int:
        """Create the layered, click-through, non-activating window."""
        if self._hwnd is not None:
            return self._hwnd
        import win32con
        import win32gui

        hinst = win32gui.GetModuleHandle(None)
        window_class = win32gui.WNDCLASS()
        window_class.hInstance = hinst
        window_class.lpfnWndProc = {win32con.WM_DESTROY: _on_destroy}
        window_class.lpszClassName = self._class_name
        window_class.hbrBackground = 0
        with contextlib.suppress(Exception):
            win32gui.RegisterClass(window_class)
        hwnd = win32gui.CreateWindowEx(
            win32con.WS_EX_LAYERED | win32con.WS_EX_TRANSPARENT
            | win32con.WS_EX_NOACTIVATE | win32con.WS_EX_TOOLWINDOW,
            self._class_name, self.agent_id, win32con.WS_POPUP, 0, 0, 10, 10,
            None, None, hinst, None)
        win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0,
                              win32con.SWP_NOMOVE | win32con.SWP_NOSIZE
                              | win32con.SWP_NOACTIVATE)
        self._hwnd = hwnd
        return hwnd

    def show(self, x: int, y: int) -> bool:
        """Draw the cursor at a screen position."""
        if not self.supported:
            return False
        import win32con
        import win32gui

        image = self.render()
        width, height = image.size
        hwnd = self._ensure_window()
        hbitmap, hdc = _dib_from_image(image)
        try:
            win32gui.UpdateLayeredWindow(
                hwnd, None, (x, y), (width, height), hdc, (0, 0), 0,
                (255, 255, 255, 255), 2)      # 2 = ULW_ALPHA
        finally:
            ctypes.windll.gdi32.DeleteObject(hbitmap)
            ctypes.windll.gdi32.DeleteDC(hdc)
        win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, x, y, width, height,
                              win32con.SWP_NOACTIVATE)
        win32gui.ShowWindow(hwnd, win32con.SW_SHOWNA)
        self.state.x = x
        self.state.y = y
        self.state.visible = True
        return True

    def hide(self) -> None:
        """Take the cursor off the screen."""
        if self._hwnd is None:
            self.state.visible = False
            return
        import win32con
        import win32gui
        win32gui.ShowWindow(self._hwnd, win32con.SW_HIDE)
        self.state.visible = False

    def close(self) -> None:
        """Destroy the window for good."""
        if self._hwnd is None:
            return
        import win32gui
        with contextlib.suppress(Exception):
            win32gui.DestroyWindow(self._hwnd)
        self._hwnd = None
        self.state.visible = False

    def __enter__(self) -> CursorOverlay:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def _on_destroy(hwnd, _message, _wparam, _lparam):  # noqa: ANN001
    import win32gui
    with contextlib.suppress(Exception):
        win32gui.DestroyWindow(hwnd)
    return True
