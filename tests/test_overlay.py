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
    CursorOverlay,
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
