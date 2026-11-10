"""Tests for the agent's own cursor.

The drawing is a pure function, so the shape can be checked without a
screen: an agent's cursor must carry that agent's colour, and it must
not move the pointer or take the focus - it is a picture.
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