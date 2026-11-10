"""Tests for the agent's own cursor.

The drawing is a pure function, so the shape can be checked without a
screen: an agent's cursor must carry that agent's colour, and it must
not move the pointer or take the focus - it is a picture.

The window plumbing cannot be checked without one, so it is checked against
the desktop it actually runs on: the DPI Windows reports for each display,
the hit test on a second display when one is attached, and the pixels the
22x22 window is really made of. What this machine cannot provide - a 100%
display, a second monitor - is skipped with the measurement printed, never
simulated.
"""
from __future__ import annotations

import contextlib
import os
import sys
import time

import pytest

from openeyes.agent.overlay import (
    CURSOR_SIZE,
    LABEL_HEIGHT,
    SHADOW,
    CursorOverlay,
    cursor_runs,
    label_width,
    render_cursor,
)
from openeyes.agent.profile import AgentProfile


def _pixels(image):
    return list(image.getdata())


def _opaque(image):
    return [px for px in _pixels(image) if px[3] > 0]


# ------------------------------------------------------------- the rendering
def test_the_cursor_is_a_small_rgba_image():
    image = render_cursor()
    assert image.mode == "RGBA"
    assert image.size == (CURSOR_SIZE + label_width(""), max(CURSOR_SIZE,
                                                            LABEL_HEIGHT + 2))


def test_the_arrow_carries_the_agent_colour():
    image = render_cursor(color="#00c853")
    opaque = _opaque(image)
    assert opaque, "the arrow must not be invisible"
    assert any(px[:3] == (0, 200, 83) for px in opaque)


def test_a_label_makes_the_cursor_wider():
    plain = render_cursor(label="")
    named = render_cursor(label="worker")
    assert named.size[0] > plain.size[0]
    assert label_width("worker") > label_width("")


def test_the_background_stays_transparent():
    """Only the cursor is drawn: the overlay must not paint a box."""
    image = render_cursor(color="#00c853", label="")
    corner = image.getpixel((image.size[0] - 1, 0))
    assert corner[3] == 0


def test_an_unknown_colour_falls_back_rather_than_failing():
    image = render_cursor(color="not-a-colour")
    assert _opaque(image)


# ----------------------------------------------------------------- the agent
def test_the_overlay_takes_its_colour_from_the_identity():
    overlay = CursorOverlay(agent_id="worker-a")
    profile = AgentProfile(agent_id="worker-a")
    assert overlay.color == profile.color
    assert overlay.agent_id == "worker-a"


def test_an_explicit_colour_wins():
    overlay = CursorOverlay(agent_id="worker-a", color="#010203")
    assert overlay.color == "#010203"


def test_two_agents_look_different():
    one = CursorOverlay(agent_id="worker-a")
    two = CursorOverlay(agent_id="worker-b")
    assert one.color != two.color


def test_the_overlay_starts_hidden():
    overlay = CursorOverlay(agent_id="worker-a")
    assert overlay.state.visible is False
    assert overlay.state.agent_id == "worker-a"


def test_the_overlay_reports_what_it_can_do():
    overlay = CursorOverlay(agent_id="worker-a")
    assert overlay.supported is sys.platform.startswith("win")


@pytest.mark.skipif(not sys.platform.startswith("win"),
                    reason="the overlay window is a Windows thing")
def test_showing_the_overlay_does_not_take_the_focus():
    """The user's foreground window must survive an agent's cursor."""
    import win32gui

    before = int(win32gui.GetForegroundWindow())
    overlay = CursorOverlay(agent_id="worker-a", label="A", color="#00c853")
    try:
        overlay.show(200, 200)
        assert int(win32gui.GetForegroundWindow()) == before
        assert overlay.state.visible is True
        overlay.hide()
        assert overlay.state.visible is False
    finally:
        overlay.close()


# ------------------------------------------------------------- the silhouette
def test_the_silhouette_covers_exactly_the_painted_pixels():
    """The window region must be the cursor and nothing else."""
    image = render_cursor(color="#00c853", label="A")
    runs = cursor_runs(image)
    width = image.size[0]
    painted = {(index % width, index // width)
               for index, pixel in enumerate(image.getdata()) if pixel[3]}
    covered = set()
    for left, top, right, bottom in runs:
        for y in range(top, bottom):
            for x in range(left, right):
                covered.add((x, y))
    assert covered == painted
    assert runs, "the cursor must have a shape"


def test_the_silhouette_leaves_the_corner_alone():
    """Nothing but the cursor belongs to the window."""
    image = render_cursor(color="#00c853", label="")
    runs = cursor_runs(image)
    width, height = image.size
    covered = {(x, y) for left, top, right, bottom in runs
               for x in range(left, right) for y in range(top, bottom)}
    assert (width - 1, height - 1) not in covered
    assert (width - 1, 0) not in covered


def test_every_painted_pixel_is_fully_opaque():
    """A window that is not layered cannot blend with the desktop.

    A translucent pixel would be painted as a flat, wrong colour, so
    the drawing has to be opaque wherever it paints at all.
    """
    image = render_cursor(color="#00c853", label="A")
    alphas = {pixel[3] for pixel in image.getdata() if pixel[3]}
    assert alphas <= {255}


def test_the_cursor_has_no_translucent_shadow():
    image = render_cursor(color="#00c853", label="")
    shadow = {pixel for pixel in image.getdata()
              if pixel[3] and pixel[:3] == SHADOW[:3]}
    assert shadow, "the arrow keeps a hard shadow"
    assert all(pixel[3] == 255 for pixel in shadow)


# --------------------------------------------------------------- the window
@pytest.mark.skipif(not sys.platform.startswith("win"),
                    reason="the overlay window is a Windows thing")
def test_the_cursor_window_is_not_layered():
    """A layered window would not reach the screen on this desktop."""
    import win32con
    import win32gui

    overlay = CursorOverlay(agent_id="worker-a", label="A", color="#00c853")
    try:
        overlay.show(200, 200)
        ex_style = win32gui.GetWindowLong(overlay._hwnd, win32con.GWL_EXSTYLE)
        assert not ex_style & win32con.WS_EX_LAYERED
        assert ex_style & win32con.WS_EX_NOACTIVATE
    finally:
        overlay.close()


@pytest.mark.skipif(not sys.platform.startswith("win"),
                    reason="the overlay window is a Windows thing")
def test_the_cursor_window_is_click_through():
    """The mouse must go straight through the cursor to the desktop."""
    import win32con
    import win32gui

    overlay = CursorOverlay(agent_id="worker-a", label="A", color="#00c853")
    try:
        overlay.show(200, 200)
        hit = win32gui.SendMessage(overlay._hwnd, win32con.WM_NCHITTEST, 0, 0)
        assert hit == win32con.HTTRANSPARENT
    finally:
        overlay.close()


# --------------------------------------------------------- the real hit path
# The test above proves what the window *says*. Windows decides what to do
# with the answer, so the tests below put a window of our own underneath the
# cursor and ask the questions the mouse asks.

#: The window class the tests use for "something underneath".
_TARGET_CLASS = "OpenEyesTestTarget"

#: The clicks the target windows received, by window handle.
_CLICKS: dict[int, list[tuple[int, int]]] = {}

_target_registered = False


#: Opt-in: the real pointer is only moved when this is set.
_REAL_CLICK = os.environ.get("OPENEYES_REAL_CLICK", "") == "1"


def _target_proc(hwnd, message, wparam, lparam):  # noqa: ANN001
    """Count the clicks, and take nothing else from the system."""
    import win32con
    import win32gui

    if message == win32con.WM_LBUTTONDOWN:
        _CLICKS.setdefault(hwnd, []).append(
            (lparam & 0xFFFF, (lparam >> 16) & 0xFFFF))
        return 0
    if message == win32con.WM_LBUTTONUP:
        return 0
    return win32gui.DefWindowProc(hwnd, message, wparam, lparam)


def _register_target() -> None:
    """Register the target window class once."""
    global _target_registered
    if _target_registered:
        return
    import win32con
    import win32gui

    window_class = win32gui.WNDCLASS()
    window_class.hInstance = win32gui.GetModuleHandle(None)
    window_class.lpfnWndProc = {
        win32con.WM_LBUTTONDOWN: _target_proc,
        win32con.WM_LBUTTONUP: _target_proc,
    }
    window_class.lpszClassName = _TARGET_CLASS
    window_class.hbrBackground = win32con.COLOR_WINDOW + 1
    with contextlib.suppress(win32gui.error):
        win32gui.RegisterClass(window_class)
    _target_registered = True


def _silhouette_region(image):
    """A window region cut to the drawing, built the plain way."""
    import win32con
    import win32gui

    region = win32gui.CreateRectRgnIndirect((0, 0, 0, 0))
    for left, top, right, bottom in cursor_runs(image):
        piece = win32gui.CreateRectRgnIndirect((left, top, right, bottom))
        win32gui.CombineRgn(region, region, piece, win32con.RGN_OR)
    return region


def _pump(wait: float = 0.0) -> None:
    """Let this thread deliver whatever the windows have queued."""
    import win32gui

    deadline = time.monotonic() + wait
    while True:
        win32gui.PumpWaitingMessages()
        if time.monotonic() >= deadline:
            return
        time.sleep(0.01)


def _wait_for_click(hwnd, timeout: float = 2.0) -> bool:
    """Pump until the window has counted a click, or the time is up."""
    import win32gui

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        win32gui.PumpWaitingMessages()
        if _CLICKS.get(hwnd):
            return True
        time.sleep(0.01)
    win32gui.PumpWaitingMessages()
    return bool(_CLICKS.get(hwnd))


@contextlib.contextmanager
def _target_window(x: int = 140, y: int = 140, width: int = 360,
                   height: int = 260, image=None):
    """A plain, non-activating window of our own that counts clicks.

    Non-activating on purpose: the foreground window must be able to
    stay exactly what it was while a window of ours is clicked.
    """
    import win32con
    import win32gui

    _register_target()
    hwnd = win32gui.CreateWindowEx(
        win32con.WS_EX_TOPMOST | win32con.WS_EX_TOOLWINDOW
        | win32con.WS_EX_NOACTIVATE,
        _TARGET_CLASS, "openeyes test target",
        win32con.WS_POPUP | win32con.WS_CAPTION | win32con.WS_SYSMENU,
        x, y, width, height, None, None, win32gui.GetModuleHandle(None), None)
    try:
        if image is not None:
            win32gui.SetWindowRgn(hwnd, _silhouette_region(image), True)
        win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, x, y, width,
                              height, win32con.SWP_NOACTIVATE
                              | win32con.SWP_SHOWWINDOW)
        win32gui.UpdateWindow(hwnd)
        _pump()
        yield hwnd
    finally:
        with contextlib.suppress(Exception):
            win32gui.DestroyWindow(hwnd)
        _pump()


def _solid_pixel(image) -> tuple[int, int]:
    """A painted pixel whose neighbours are painted too.

    It is unambiguously inside the silhouette, so a point outside the
    shape cannot be mistaken for it.
    """
    alpha = image.load()
    width, height = image.size
    for y in range(1, height - 1):
        for x in range(1, width - 1):
            if all(alpha[x + dx, y + dy]
                   for dx, dy in ((0, 0), (1, 0), (-1, 0), (0, 1), (0, -1))):
                return (x, y)
    raise AssertionError("the cursor has no interior pixel")


def _painted_pixels(image) -> list[tuple[int, int]]:
    width, height = image.size
    return [(x, y) for y in range(height) for x in range(width)
            if image.getpixel((x, y))[3]]


@pytest.mark.skipif(not sys.platform.startswith("win"),
                    reason="the overlay window is a Windows thing")
def test_the_real_hit_test_skips_the_cursor():
    """WindowFromPoint must land on the window below, not on the cursor.

    Asking the overlay what its window procedure would answer proves
    what the window says, not what Windows does with the answer. So
    this asks the question the mouse asks: a window of our own, the
    cursor drawn over its client area, and WindowFromPoint at a point
    on the drawn arrow - sampled before the cursor appears and again
    after.

    The overlay is not layered; its region is the silhouette, so a
    point outside the silhouette would fall through for a different
    reason. The point used here is checked to be inside the region and
    the blank corner is checked to be outside it, so both halves are
    covered: the silhouette is skipped because of HTTRANSPARENT, and
    the rest of the rectangle is not part of the window at all.
    """
    import win32api
    import win32con
    import win32gui

    from openeyes.core.dpi import ensure_dpi_awareness

    ensure_dpi_awareness()
    image = render_cursor(CURSOR_SIZE, "#00c853", "")
    body = _solid_pixel(image)
    tail = max(_painted_pixels(image), key=lambda pixel: pixel[1])
    corner = (image.size[0] - 1, image.size[1] - 1)
    width, height = image.size

    overlay = CursorOverlay(agent_id="worker-a", label="", color="#00c853")
    try:
        with _target_window() as target:
            left, top = win32gui.ClientToScreen(target, (0, 0))
            origin = (left + 60, top + 60)
            point = (origin[0] + body[0], origin[1] + body[1])
            tail_point = (origin[0] + tail[0], origin[1] + tail[1])
            corner_point = (origin[0] + corner[0], origin[1] + corner[1])

            assert win32gui.WindowFromPoint(point) == target, (
                "control: the window below is what the mouse finds")

            assert overlay.show(*origin) is True
            _pump()
            hwnd = overlay._hwnd
            assert hwnd and win32gui.IsWindowVisible(hwnd), (
                "the cursor must really be on the screen")
            assert win32gui.GetWindowRect(hwnd) == (
                origin[0], origin[1], origin[0] + width, origin[1] + height)

            region = win32gui.CreateRectRgnIndirect((0, 0, 0, 0))
            assert win32gui.GetWindowRgn(hwnd, region) != 0, (
                "the window must be cut to the silhouette")
            assert win32gui.PtInRegion(region, *body), (
                "the sampled point is inside the window")
            assert win32gui.PtInRegion(region, *tail)
            assert not win32gui.PtInRegion(region, *corner), (
                "the blank corner is not part of the window")

            assert win32gui.WindowFromPoint(point) == target, (
                "the mouse must still find the window below")
            assert win32gui.WindowFromPoint(tail_point) == target
            assert win32gui.WindowFromPoint(point) != hwnd

            for x, y in (point, tail_point, corner_point):
                hit = win32gui.SendMessage(hwnd, win32con.WM_NCHITTEST, 0,
                                           win32api.MAKELONG(x, y))
                assert hit == win32con.HTTRANSPARENT, (
                    f"the cursor must answer HTTRANSPARENT at {(x, y)}")
    finally:
        overlay.close()


@pytest.mark.skipif(not sys.platform.startswith("win"),
                    reason="the overlay window is a Windows thing")
def test_a_window_of_the_same_shape_without_httransparent_is_hit():
    """The answer is what makes the mouse reach through, not the shape.

    A control for the test above. An ordinary window of the same
    class, cut to the same silhouette, at the same place, with the
    same styles, topmost above the same target window - it only
    answers HTCLIENT, the way an ordinary window does. WindowFromPoint
    must return it.

    Without this, "the overlay is skipped" could mean "a topmost
    non-activating tool window is invisible to hit testing", which
    would say nothing about the cursor.
    """
    import win32gui

    from openeyes.core.dpi import ensure_dpi_awareness

    ensure_dpi_awareness()
    image = render_cursor(CURSOR_SIZE, "#00c853", "")
    body = _solid_pixel(image)
    width, height = image.size

    with _target_window() as target:
        left, top = win32gui.ClientToScreen(target, (0, 0))
        origin = (left + 60, top + 60)
        point = (origin[0] + body[0], origin[1] + body[1])
        assert win32gui.WindowFromPoint(point) == target
        with _target_window(origin[0], origin[1], width, height,
                            image=image) as control:
            region = win32gui.CreateRectRgnIndirect((0, 0, 0, 0))
            assert win32gui.GetWindowRgn(control, region) != 0
            assert win32gui.PtInRegion(region, *body)
            assert win32gui.WindowFromPoint(point) == control, (
                "a window that does not answer HTTRANSPARENT is hit")
            assert win32gui.WindowFromPoint(point) != target


@pytest.mark.skipif(not sys.platform.startswith("win"),
                    reason="the overlay window is a Windows thing")
@pytest.mark.skipif(not _REAL_CLICK,
                    reason="opt-in: set OPENEYES_REAL_CLICK=1 to move the real pointer")
def test_a_real_click_reaches_the_window_under_the_cursor():
    """Opt-in: the real pointer, clicking, straight through the cursor.

    This one moves the mouse and presses a button, so it only runs
    when OPENEYES_REAL_CLICK=1. It is the only check that the whole
    input path agrees with the window procedure: the pointer, the hit
    test and the click, with the foreground window watched before the
    cursor appears, after it appears, and after the click.

    A click that never arrives proves nothing, so it is not counted as
    a pass: if a click beside the cursor is not delivered either, this
    session cannot inject input and the test says so and skips.
    """
    import win32api
    import win32con
    import win32gui

    from openeyes.core.dpi import ensure_dpi_awareness

    ensure_dpi_awareness()
    if not win32gui.GetForegroundWindow():
        pytest.skip("no interactive desktop: nothing is in the foreground")

    image = render_cursor(CURSOR_SIZE, "#00c853", "")
    body = _solid_pixel(image)
    saved_cursor = win32gui.GetCursorPos()
    overlay = CursorOverlay(agent_id="worker-a", label="", color="#00c853")
    try:
        with _target_window() as target:
            left, top = win32gui.ClientToScreen(target, (0, 0))
            origin = (left + 60, top + 60)
            point = (origin[0] + body[0], origin[1] + body[1])
            away = (left + 30, top + 200)
            foreground = win32gui.GetForegroundWindow()

            assert overlay.show(*origin) is True
            _pump()
            hwnd = overlay._hwnd
            width, height = image.size
            assert hwnd and win32gui.IsWindowVisible(hwnd)
            assert win32gui.GetWindowRect(hwnd) == (
                origin[0], origin[1], origin[0] + width, origin[1] + height)
            region = win32gui.CreateRectRgnIndirect((0, 0, 0, 0))
            assert win32gui.GetWindowRgn(hwnd, region) != 0
            assert win32gui.PtInRegion(region, *body), (
                "the click point must be under the cursor, not beside it")
            assert win32gui.GetForegroundWindow() == foreground, (
                "showing the cursor must not take the focus")

            win32api.SetCursorPos(point)
            if tuple(win32gui.GetCursorPos()) != point:
                pytest.skip("this session will not move the pointer")

            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
            if not _wait_for_click(target):
                win32api.SetCursorPos(away)
                win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
                win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
                if not _wait_for_click(target):
                    pytest.skip("this session does not deliver clicks to its own windows")
                pytest.fail("the click never reached the window below the cursor")

            assert _CLICKS[target][0] == win32gui.ScreenToClient(target, point), (
                "the click that arrived is the one aimed at the cursor")
            assert win32gui.WindowFromPoint(point) != overlay._hwnd
            assert win32gui.GetForegroundWindow() == foreground, (
                "the click must not move the focus")
    finally:
        win32api.SetCursorPos(tuple(saved_cursor))
        overlay.close()


# ------------------------------------------- dpi, and a second display
# The tests above ran wherever the target window happened to sit, on
# whatever scaling this machine has. A window region is in physical pixels
# and a scaled display is exactly where the physical and logical spaces can
# drift apart, so the tests below ask the desktop what it is - how many
# displays, at which DPI - and run the same region and hit-test assertions
# there. A 100% display or a second monitor that this machine does not have
# is skipped with the measurement printed, never simulated.


def _displays() -> list[dict]:
    """Every display Windows reports: rectangles, DPI and scale percentage.

    ``GetDpiForMonitor`` answers the effective DPI the window manager uses
    for that monitor, ``GetScaleFactorForMonitor`` the percentage the
    Settings app shows. Both are asked, after ``ensure_dpi_awareness``: a
    DPI-unaware caller is answered in the virtualised space and would read
    96 dpi for a scaled display, which is the one answer this must not get
    wrong.
    """
    import ctypes
    from ctypes import wintypes

    from openeyes.core.dpi import ensure_dpi_awareness

    ensure_dpi_awareness()

    class Rect(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                    ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    class MonitorInfo(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", Rect),
                    ("rcWork", Rect), ("dwFlags", wintypes.DWORD),
                    ("szDevice", ctypes.c_wchar * 32)]

    callback = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p,
                                  ctypes.c_void_p, ctypes.POINTER(Rect),
                                  ctypes.c_double)
    found: list[dict] = []

    def collect(hmonitor, _hdc, _rect, _data):
        info = MonitorInfo()
        info.cbSize = ctypes.sizeof(info)
        if not ctypes.windll.user32.GetMonitorInfoW(hmonitor,
                                                    ctypes.byref(info)):
            return 1
        dpi_x, dpi_y = ctypes.c_uint(), ctypes.c_uint()
        has_dpi = ctypes.windll.shcore.GetDpiForMonitor(
            ctypes.c_void_p(hmonitor), 0, ctypes.byref(dpi_x),
            ctypes.byref(dpi_y)) == 0
        scale = ctypes.c_int()
        has_scale = ctypes.windll.shcore.GetScaleFactorForMonitor(
            ctypes.c_void_p(hmonitor), ctypes.byref(scale)) == 0
        found.append({
            "device": info.szDevice,
            "monitor": (info.rcMonitor.left, info.rcMonitor.top,
                         info.rcMonitor.right, info.rcMonitor.bottom),
            "work": (info.rcWork.left, info.rcWork.top,
                     info.rcWork.right, info.rcWork.bottom),
            "primary": bool(info.dwFlags & 1),
            "dpi": dpi_x.value if has_dpi else None,
            "scale": scale.value if has_scale else None,
        })
        return 1

    ctypes.windll.user32.EnumDisplayMonitors(None, None, callback(collect), 0)
    return found


def _describe(display: dict) -> str:
    """One display in a line, for a skip reason or a failure message."""
    dpi = f"{display['dpi']} dpi" if display["dpi"] else "dpi unknown"
    scale = f" ({display['scale']}%)" if display["scale"] else ""
    return (f"{display['device']} {display['monitor']} {dpi}{scale}"
            + (", primary" if display["primary"] else ""))


def _window_dpi(hwnd: int) -> int:
    """The DPI the window manager reports for one window.

    ``GetDpiForWindow`` through ctypes: the ``win32gui`` of this pywin32
    does not wrap it. ``GetDeviceCaps(hdc, LOGPIXELSX)`` is the fallback,
    the same number read from the window's own device context.
    """
    import ctypes

    user32 = ctypes.windll.user32
    try:
        user32.GetDpiForWindow.restype = ctypes.c_uint
        user32.GetDpiForWindow.argtypes = [ctypes.c_void_p]
        dpi = int(user32.GetDpiForWindow(hwnd))
        if dpi:
            return dpi
    except Exception:                            # pragma: no cover - old OS
        pass
    hdc = user32.GetDC(hwnd)
    try:
        return int(ctypes.windll.gdi32.GetDeviceCaps(hdc, 88))  # LOGPIXELSX
    finally:
        user32.ReleaseDC(hwnd, hdc)


def _target_corner(display: dict, width: int = 360,
                   height: int = 260) -> tuple[int, int, int, int]:
    """A corner of that display's work area for the test target window."""
    left, top, right, bottom = display["work"]
    width = min(width, (right - left) - 120)
    height = min(height, (bottom - top) - 120)
    assert width >= 200 and height >= 200, (
        f"{_describe(display)} is too small to hold this test's window")
    return left + 40, top + 40, width, height


def _assert_cursor_is_see_through_over(target, origin, image,
                                       where: str = "") -> dict:
    """Draw the cursor at ``origin`` over ``target`` and ask the hit test.

    The four things a click depends on, in one place: the window is the
    drawing's own size, its region is the silhouette, the mouse finds
    ``target`` through it, and the cursor window answers HTTRANSPARENT.
    Returns what was measured (window rectangle, window DPI, region box)
    so the caller can check it against the display it aimed at.
    """
    import win32api
    import win32con
    import win32gui

    body = _solid_pixel(image)
    corner = (image.size[0] - 1, image.size[1] - 1)
    width, height = image.size
    point = (origin[0] + body[0], origin[1] + body[1])

    overlay = CursorOverlay(agent_id="worker-a", label="", color="#00c853")
    try:
        assert win32gui.WindowFromPoint(point) == target, (
            f"{where}: the window below must be what the mouse finds before "
            "the cursor is drawn")
        assert overlay.show(*origin) is True, (
            f"{where}: the cursor must show at {origin}")
        _pump()
        hwnd = overlay._hwnd
        assert hwnd and win32gui.IsWindowVisible(hwnd), (
            f"{where}: the cursor must really be on the screen")
        rect = win32gui.GetWindowRect(hwnd)
        assert rect == (origin[0], origin[1], origin[0] + width,
                        origin[1] + height), (
            f"{where}: the window must be exactly the drawing at {origin}, "
            f"not {rect}")

        region = win32gui.CreateRectRgnIndirect((0, 0, 0, 0))
        assert win32gui.GetWindowRgn(hwnd, region) != 0, (
            f"{where}: the window must be cut to the silhouette")
        assert win32gui.PtInRegion(region, *body), (
            f"{where}: the sampled point is inside the window")
        assert not win32gui.PtInRegion(region, *corner), (
            f"{where}: the blank corner is not part of the window")

        # Every pixel, not just the two above: one interior point and one
        # corner are also satisfied by a plain rectangle, and the point of
        # the region is that the window *is* the drawing.
        painted = [(x, y) for y in range(height) for x in range(width)
                   if image.getpixel((x, y))[3]]
        blank = [(x, y) for y in range(height) for x in range(width)
                 if not image.getpixel((x, y))[3]]
        missing = [(x, y) for x, y in painted
                   if not win32gui.PtInRegion(region, x, y)]
        leaked = [(x, y) for x, y in blank
                  if win32gui.PtInRegion(region, x, y)]
        assert not missing, (
            f"{where}: painted pixels left out of the window region: "
            f"{missing[:6]}")
        assert not leaked, (
            f"{where}: unpainted pixels inside the window region: "
            f"{leaked[:6]}")

        assert win32gui.WindowFromPoint(point) == target, (
            f"{where}: the mouse must still find the window below")
        assert win32gui.WindowFromPoint(point) != hwnd
        assert win32gui.SendMessage(hwnd, win32con.WM_NCHITTEST, 0,
                                    win32api.MAKELONG(*point)) \
            == win32con.HTTRANSPARENT, (
                f"{where}: the cursor must answer HTTRANSPARENT at {point}")
        return {"rect": rect,
                "window_dpi": _window_dpi(hwnd),
                "region_box": win32gui.GetRgnBox(region)}
    finally:
        overlay.close()


@pytest.mark.skipif(not sys.platform.startswith("win"),
                    reason="the overlay window is a Windows thing")
def test_the_region_and_hit_path_hold_at_every_display_scaling():
    """The same region and hit-test assertions, once per display.

    Each display is asked for its own DPI, the target window and the
    cursor are placed inside that display, and the window's DPI is
    checked back against the display's: with per-monitor awareness those
    two numbers have to agree, and on a scaled display they are the reason
    a logical/physical mix-up would show up as a miss instead of a
    rounding difference.
    """
    import win32gui

    from openeyes.core.dpi import ensure_dpi_awareness

    ensure_dpi_awareness()
    displays = _displays()
    assert displays, "EnumDisplayMonitors reported no display at all"
    image = render_cursor(CURSOR_SIZE, "#00c853", "")

    for display in displays:
        where = _describe(display)
        with _target_window(*_target_corner(display)) as target:
            left, top = win32gui.ClientToScreen(target, (0, 0))
            measured = _assert_cursor_is_see_through_over(
                target, (left + 60, top + 60), image, where=where)
            assert measured["window_dpi"] == display["dpi"], (
                f"{where}: the cursor window reports "
                f"{measured['window_dpi']} dpi, the display {display['dpi']}")
            assert measured["rect"][2] - measured["rect"][0] \
                == image.size[0]
            assert measured["rect"][3] - measured["rect"][1] \
                == image.size[1]
            print(f"{where}: region + hit test passed, "
                  f"GetDpiForWindow={measured['window_dpi']}")


@pytest.mark.skipif(not sys.platform.startswith("win"),
                    reason="the overlay window is a Windows thing")
def test_the_region_and_hit_path_at_100_percent_scaling():
    """The same assertions on a 100% display, if one is attached.

    This machine has one display at 130%, so this test skips and says so
    with the measured DPI instead of pretending a scaled display is an
    unscaled one: a real 100% run needs the system display scale changed,
    which is not something a test may do to somebody's desktop.
    """
    import win32gui

    from openeyes.core.dpi import ensure_dpi_awareness

    ensure_dpi_awareness()
    displays = _displays()
    hundred = [display for display in displays if display["dpi"] == 96]
    if not hundred:
        pytest.skip("no 100% display attached; measured: "
                    + "; ".join(_describe(display) for display in displays)
                    + " - an unscaled run needs the system display scale "
                      "changed, which this test will not do")

    display = hundred[0]
    where = _describe(display)
    image = render_cursor(CURSOR_SIZE, "#00c853", "")
    with _target_window(*_target_corner(display)) as target:
        left, top = win32gui.ClientToScreen(target, (0, 0))
        measured = _assert_cursor_is_see_through_over(
            target, (left + 60, top + 60), image, where=where)
        assert measured["window_dpi"] == 96
        assert measured["rect"][2] - measured["rect"][0] == image.size[0]
        assert measured["rect"][3] - measured["rect"][1] == image.size[1]


@pytest.mark.skipif(not sys.platform.startswith("win"),
                    reason="the overlay window is a Windows thing")
def test_the_region_and_hit_path_on_a_second_display():
    """Target and cursor on the second display's origin: the hit test holds.

    A window placed with negative or far-away virtual-screen coordinates
    is where a virtualised rectangle, a stitched-together screen or a
    per-monitor DPI choice would go wrong. The point that is checked is
    inside both the display and the drawn arrow.
    """
    import win32gui

    from openeyes.core.dpi import ensure_dpi_awareness

    ensure_dpi_awareness()
    displays = _displays()
    if len(displays) < 2:
        pytest.skip("a second display is needed for this test; measured: "
                    + "; ".join(_describe(display) for display in displays))

    display = next((item for item in displays if not item["primary"]),
                   displays[1])
    where = _describe(display)
    image = render_cursor(CURSOR_SIZE, "#00c853", "")
    with _target_window(*_target_corner(display)) as target:
        left, top = win32gui.ClientToScreen(target, (0, 0))
        origin = (left + 60, top + 60)
        assert display["work"][0] <= origin[0] < display["work"][2], (
            f"{where}: the cursor must land on the second display, "
            f"not at {origin}")
        assert display["work"][1] <= origin[1] < display["work"][3]
        measured = _assert_cursor_is_see_through_over(target, origin, image,
                                                      where=where)
        assert measured["window_dpi"] == display["dpi"], (
            f"{where}: the cursor window must take the second display's "
            f"dpi {display['dpi']}, not {measured['window_dpi']}")
        print(f"{where}: region + hit test passed at {origin}")


@pytest.mark.skipif(not sys.platform.startswith("win"),
                    reason="the overlay window is a Windows thing")
def test_the_cursor_window_is_sized_in_physical_pixels():
    """22x22 is 22 physical pixels, measured against the display's DPI.

    Measured on this machine (2026-11-10, one display at 130%): the
    drawing is 22x22, ``GetWindowRect`` is 22x22, ``GetDpiForWindow`` is
    125 dpi, so the window is 22 physical pixels = ~16.9 device-
    independent pixels. A DPI-scaled 22 DIP cursor would be ~28.7 px.

    That is not a coordinate mix-up: the bitmap, the window size and the
    region runs are all the same physical pixels, which is why the hit
    test at a drawn pixel is exact - the test above proves that at this
    scaling. What it does mean is that the cursor's *apparent* size does
    not follow the display's scaling; making it constant in DIPs would
    mean drawing at ``CURSOR_SIZE * dpi / 96`` (the region follows the
    image, so nothing else would have to change). That is a product
    decision about how big an agent's cursor should look, not a bug in
    the coordinates, so this test pins what is true today - 1:1 physical
    - and a later change that mixes the two spaces will fail here rather
    than on somebody's screen.
    """
    import win32gui

    from openeyes.core.dpi import ensure_dpi_awareness

    ensure_dpi_awareness()
    displays = _displays()
    assert displays, "EnumDisplayMonitors reported no display at all"
    display = next((item for item in displays if item["primary"]),
                   displays[0])
    where = _describe(display)

    image = render_cursor(CURSOR_SIZE, "#00c853", "")
    overlay = CursorOverlay(agent_id="worker-a", label="", color="#00c853")
    try:
        x, y = _target_corner(display)[:2]
        assert overlay.show(x, y) is True
        _pump()
        hwnd = overlay._hwnd
        rect = win32gui.GetWindowRect(hwnd)
        width, height = rect[2] - rect[0], rect[3] - rect[1]
        dpi = _window_dpi(hwnd)

        assert (width, height) == image.size, (
            f"{where}: the window is {width}x{height}, the drawing is "
            f"{image.size}")
        assert win32gui.GetClientRect(hwnd) == (0, 0, width, height)
        assert image.size == (CURSOR_SIZE + label_width(""),
                              max(CURSOR_SIZE, LABEL_HEIGHT + 2)), (
            "the drawing was not scaled: CURSOR_SIZE is a pixel count")
        assert dpi == display["dpi"], (
            f"{where}: GetDpiForWindow said {dpi}, the display "
            f"{display['dpi']}")
        if dpi and dpi != 96:
            scaled = round(CURSOR_SIZE * dpi / 96)
            assert width != scaled, (
                f"{where}: a window of {scaled}px would be a DPI-scaled "
                "CURSOR_SIZE; this overlay draws physical pixels")
        region = win32gui.CreateRectRgnIndirect((0, 0, 0, 0))
        assert win32gui.GetWindowRgn(hwnd, region) != 0
        _kind, (left, top, right, bottom) = win32gui.GetRgnBox(region)
        assert (left, top) >= (0, 0) and (right, bottom) <= (width, height), (
            f"{where}: the region {(left, top, right, bottom)} must stay "
            f"inside the {width}x{height} window")

        print(f"{where}: window {width}x{height} physical, dpi {dpi}, "
              f"= {CURSOR_SIZE * 96 / dpi:.1f} DIP; a DPI-scaled cursor "
              f"would be {round(CURSOR_SIZE * dpi / 96)}px")
    finally:
        overlay.close()