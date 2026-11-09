"""The agent's own cursor: drawn, never driven.

A mouse cluster is only legible if you can tell who is moving. The
system pointer can only be one, so the agents draw their own: a small
cursor per agent, coloured by identity, floating over the desktop.

Two rules make this safe to put on somebody's screen:

* **it never drives the pointer.** The overlay is a picture. It does
  not move the mouse, it does not click, it does not own the input.
* **it never takes the foreground.** The window is click-through and
  non-activating, so the user's own window keeps the focus while an
  agent's cursor is drawn on top of it.

How the picture reaches the screen is not a free choice. The obvious
way is a window with per-pixel transparency (``WS_EX_LAYERED``), and
on most desktops that works. On some it never reaches the screen at
all, and this workstation is one of them: the desktop compositor
refuses to draw layered windows, while ordinary windows land exactly
as they should. Measured here - a 100x60 plain window arrived with
all 6000 of its pixels, while every layered variant (``ULW_ALPHA``
in three call orders, ``ULW_COLORKEY``, ``SetLayeredWindowAttributes``
with either alpha or colour key, topmost or not) arrived with none,
and three independent ways of reading the screen agreed on it.

So the cursor is an ordinary window whose *shape* is the cursor: the
window region is cut to the silhouette of the drawing, and the window
paints the drawing inside it. Everything outside the silhouette is
not part of the window, so the desktop shows through.

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
OUTLINE = (255, 255, 255, 255)

#: The colour of the drop shadow under the arrow.
SHADOW = (32, 32, 32, 255)

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

    Every pixel that carries paint is fully opaque. The window that
    shows the drawing is not a layered one, so it cannot blend with
    the desktop - a translucent edge would be painted as a flat,
    wrong colour instead. An opaque drawing with a white outline and
    a hard shadow reads the way a system cursor does.
    """
    width = size + label_width(label)
    height = max(size, LABEL_HEIGHT + 2)
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    points = _arrow_points(size)
    shadow = [(x + 1, y + 1) for x, y in points]
    draw.polygon(shadow, fill=SHADOW)
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


def cursor_runs(image: Image.Image) -> list[tuple[int, int, int, int]]:
    """The silhouette of a drawing, as horizontal runs of pixels.

    Each row is cut into the stretches that carry paint. Those
    stretches are what the window region is built from, so the window
    is exactly the shape of the cursor and nothing else - the desktop
    shows through everywhere else.
    """
    alpha = image.getchannel("A")
    width, height = image.size
    pixels = alpha.load()
    runs: list[tuple[int, int, int, int]] = []
    for y in range(height):
        x = 0
        while x < width:
            if pixels[x, y]:
                start = x
                while x < width and pixels[x, y]:
                    x += 1
                runs.append((start, y, x, y + 1))
            else:
                x += 1
    return runs


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

    Returns ``(hbitmap, hdc)``: the caller must delete both when it
    is done with them.
    """
    gdi32 = ctypes.windll.gdi32
    gdi32.CreateDIBSection.restype = ctypes.c_void_p
    gdi32.CreateDIBSection.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(_BitmapInfo), wintypes.UINT,
        ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, wintypes.DWORD,
    ]
    gdi32.SelectObject.restype = ctypes.c_void_p
    gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    gdi32.DeleteObject.restype = ctypes.c_void_p
    gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
    gdi32.DeleteDC.restype = ctypes.c_void_p
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


def _region_from_runs(runs: list[tuple[int, int, int, int]]) -> int:
    """Cut one window region out of the runs of a drawing."""
    gdi32 = ctypes.windll.gdi32
    gdi32.CreateRectRgn.restype = ctypes.c_void_p
    gdi32.CreateRectRgn.argtypes = [ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int]
    gdi32.CombineRgn.restype = ctypes.c_void_p
    gdi32.CombineRgn.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                 ctypes.c_void_p, ctypes.c_int]
    gdi32.DeleteObject.restype = ctypes.c_void_p
    gdi32.DeleteObject.argtypes = [ctypes.c_void_p]

    region = gdi32.CreateRectRgn(0, 0, 0, 0)
    for left, top, right, bottom in runs:
        piece = gdi32.CreateRectRgn(left, top, right, bottom)
        merged = gdi32.CombineRgn(gdi32.CreateRectRgn(0, 0, 0, 0),
                                  region, piece, 2)      # RGN_OR
        gdi32.DeleteObject(region)
        gdi32.DeleteObject(piece)
        region = merged
    return region


#: The one window class every agent cursor uses.
_CLASS_NAME = "OpenEyesCursor"

#: The live cursors, by window handle, so the window procedures can
#: find the cursor a message is about.
_WINDOWS: dict[int, CursorOverlay] = {}

_class_registered = False


def _register_class(hinst) -> None:
    """Register the shared cursor window class, once."""
    global _class_registered
    if _class_registered:
        return
    import win32con
    import win32gui

    window_class = win32gui.WNDCLASS()
    window_class.hInstance = hinst
    window_class.lpfnWndProc = {
        win32con.WM_DESTROY: _on_destroy,
        win32con.WM_PAINT: _on_paint,
        win32con.WM_NCHITTEST: _on_nchittest,
    }
    window_class.lpszClassName = _CLASS_NAME
    window_class.hbrBackground = 0
    with contextlib.suppress(Exception):
        win32gui.RegisterClass(window_class)
    _class_registered = True


def _on_paint(hwnd, _message, _wparam, _lparam):  # noqa: ANN001
    """Paint the drawing into the window."""
    import win32con
    import win32gui

    overlay = _WINDOWS.get(hwnd)
    if overlay is None or overlay._hdc is None:
        return 0
    hdc, ps = win32gui.BeginPaint(hwnd)
    try:
        win32gui.BitBlt(hdc, 0, 0, overlay._width, overlay._height,
                        overlay._hdc, 0, 0, win32con.SRCCOPY)
    finally:
        win32gui.EndPaint(hwnd, ps)
    return 0


def _on_nchittest(_hwnd, _message, _wparam, _lparam):  # noqa: ANN001
    """Let the mouse through: the cursor is a picture, not a target."""
    import win32con

    return win32con.HTTRANSPARENT


def _on_destroy(hwnd, _message, _wparam, _lparam):  # noqa: ANN001
    _WINDOWS.pop(hwnd, None)
    return 0


class CursorOverlay:
    """A picture of an agent's cursor, floating over the desktop.

    The window is an ordinary topmost window whose region is cut to
    the shape of the cursor: the user clicks straight through it,
    their foreground window never changes, and the desktop shows
    through everywhere the cursor is not. It draws; it never drives.
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
        self._hbitmap = None
        self._hdc = None
        self._width = 0
        self._height = 0

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
        """Create the click-through, non-activating cursor window."""
        if self._hwnd is not None:
            return self._hwnd
        import win32con
        import win32gui

        from openeyes.core.dpi import ensure_dpi_awareness
        ensure_dpi_awareness()

        image = self.render()
        self._width, self._height = image.size
        self._hbitmap, self._hdc = _dib_from_image(image)

        hinst = win32gui.GetModuleHandle(None)
        _register_class(hinst)
        hwnd = win32gui.CreateWindowEx(
            win32con.WS_EX_TOPMOST | win32con.WS_EX_NOACTIVATE
            | win32con.WS_EX_TOOLWINDOW,
            _CLASS_NAME, self.agent_id, win32con.WS_POPUP, 0, 0,
            self._width, self._height, None, None, hinst, None)
        _WINDOWS[hwnd] = self

        region = _region_from_runs(cursor_runs(image))
        win32gui.SetWindowRgn(hwnd, region, True)
        self._hwnd = hwnd
        return hwnd

    def show(self, x: int, y: int) -> bool:
        """Draw the cursor at a screen position."""
        if not self.supported:
            return False
        import win32con
        import win32gui

        hwnd = self._ensure_window()
        win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, int(x), int(y),
                              self._width, self._height, win32con.SWP_NOACTIVATE)
        win32gui.ShowWindow(hwnd, win32con.SW_SHOWNA)
        win32gui.InvalidateRect(hwnd, None, True)
        win32gui.UpdateWindow(hwnd)
        self.state.x = int(x)
        self.state.y = int(y)
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
        if self._hwnd is not None:
            import win32gui

            with contextlib.suppress(Exception):
                win32gui.DestroyWindow(self._hwnd)
            self._hwnd = None
        if self._hdc is not None:
            with contextlib.suppress(Exception):
                ctypes.windll.gdi32.DeleteDC(self._hdc)
            self._hdc = None
        if self._hbitmap is not None:
            with contextlib.suppress(Exception):
                ctypes.windll.gdi32.DeleteObject(self._hbitmap)
            self._hbitmap = None
        self.state.visible = False

    def __enter__(self) -> CursorOverlay:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
