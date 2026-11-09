"""Tests for the agent's own cursor.

The drawing is a pure function, so the shape can be checked without a
screen: an agent's cursor must carry that agent's colour, and it must
not move the pointer or take the focus - it is a picture.
"""
from __future__ import annotations

import sys

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