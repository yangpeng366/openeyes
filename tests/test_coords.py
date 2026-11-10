"""Coordinate-space tests: the window origin must be added back exactly once.

``Element.center`` from the uia / vision backends is *window-relative* and
from cdp it is *viewport-relative*, but ``click_xy`` takes *absolute
screen* coordinates. A window that is not at (0, 0) used to shift every
physical click by its own origin. These tests pin both halves: the pure
conversion in ``openeyes.core.coords`` and the CLI seam where the bug
lived.
"""
from __future__ import annotations

import sys
import types

import pytest
from click.testing import CliRunner

from openeyes.cli.main import cli
from openeyes.core.coords import (
    MissingViewportOrigin,
    MissingWindowOrigin,
    UnknownCoordinateSpace,
    element_space,
    to_screen_point,
    to_screen_xy,
)
from openeyes.core.schema import BBox, Center, Element


def _fake_win32gui(origin: tuple[int, int]) -> types.SimpleNamespace:
    """A stand-in for win32gui whose window sits at *origin*."""
    left, top = origin

    def get_window_rect(hwnd):
        return left, top, left + 800, top + 600

    return types.SimpleNamespace(GetWindowRect=get_window_rect)


def _element(backend: str, x: int, y: int, name: str = "Save") -> Element:
    return Element(backend=backend, control_type="Button", name=name,
                   bbox=BBox(x - 5, y - 5, 10, 10), center=Center(x, y),
                   automation_id="SaveButton")


def _run(*args):
    return CliRunner().invoke(cli, list(args), catch_exceptions=False)


# ------------------------------------------------------- the CLI regression
def test_a_pixel_click_adds_the_window_origin(monkeypatch):
    """A uia element at (10, 20) in a window at (300, 200) is clicked at
    (310, 220) on screen - not at (10, 20)."""
    monkeypatch.setitem(sys.modules, "win32gui", _fake_win32gui((300, 200)))
    monkeypatch.setattr("openeyes.cli.main.detect_elements",
                        lambda *a, **k: [_element("uia", 10, 20)])
    monkeypatch.setattr("openeyes.cli.main.foreground_window", lambda: 42)
    clicked: list[tuple[int, int]] = []
    monkeypatch.setattr("openeyes.cli.main.click_xy",
                        lambda x, y, **k: clicked.append((int(x), int(y))))

    result = _run("--json", "click", "--window", "42",
                  "--name-contains", "Save", "--method", "pixel", "--go")

    assert result.exit_code == 0, result.stderr
    assert clicked == [(310, 220)]


def test_absolute_coordinates_are_not_shifted(monkeypatch):
    """``--x/--y`` is already absolute; the window origin must not be added
    twice."""
    monkeypatch.setitem(sys.modules, "win32gui", _fake_win32gui((300, 200)))
    monkeypatch.setattr("openeyes.cli.main.foreground_window", lambda: 42)
    clicked: list[tuple[int, int]] = []
    monkeypatch.setattr("openeyes.cli.main.click_xy",
                        lambda x, y, **k: clicked.append((int(x), int(y))))

    result = _run("--json", "click", "--window", "42", "--x", "310", "--y", "220",
                  "--method", "pixel", "--go")

    assert result.exit_code == 0, result.stderr
    assert clicked == [(310, 220)]


def test_an_unknown_space_is_never_clicked(monkeypatch):
    """A backend nobody taught coords.py about is a failure, not a guess."""
    monkeypatch.setitem(sys.modules, "win32gui", _fake_win32gui((300, 200)))
    monkeypatch.setattr("openeyes.cli.main.detect_elements",
                        lambda *a, **k: [_element("mystery", 10, 20)])
    monkeypatch.setattr("openeyes.cli.main.foreground_window", lambda: 42)

    def fail(*a, **k):
        raise AssertionError("an unknown coordinate space must not click")

    monkeypatch.setattr("openeyes.cli.main.click_xy", fail)

    result = _run("--json", "click", "--window", "42",
                  "--name-contains", "Save", "--method", "pixel", "--go")

    assert result.exit_code == 4
    assert "mystery" in result.stdout


# --------------------------------------------- the three spaces, one pixel
@pytest.mark.parametrize(
    ("backend", "origin_kwargs"),
    [
        ("uia", {"window_origin": (300, 200)}),
        ("vision", {"window_origin": (300, 200)}),
        ("cdp", {"window_origin": (0, 0), "viewport_origin": (300, 200)}),
    ],
)
def test_the_same_screen_target_in_three_spaces(backend, origin_kwargs):
    """(10, 20) is (310, 220) on screen, whichever backend said it.

    The viewport origin here is (300, 200) rather than the (300, 220) the
    task sheet quoted: 20 + 220 is 240, so only (300, 200) can put the
    three backends on the same pixel. The verbatim addition is pinned
    separately below.
    """
    assert to_screen_point(_element(backend, 10, 20), **origin_kwargs) == (310, 220)


def test_the_origin_is_added_verbatim():
    """A viewport origin of (300, 220) puts (10, 20) at (310, 240) - the
    conversion never silently "corrects" a caller's numbers."""
    assert to_screen_point(_element("cdp", 10, 20),
                           window_origin=(0, 0),
                           viewport_origin=(300, 220)) == (310, 240)


@pytest.mark.parametrize(
    ("backend", "space"),
    [("uia", "window"), ("vision", "window"), ("cdp", "viewport"),
     ("UIA", "window")],
)
def test_the_space_is_read_from_the_backend(backend, space):
    assert element_space(_element(backend, 0, 0)) == space


def test_an_unknown_backend_is_not_guessed():
    with pytest.raises(UnknownCoordinateSpace) as excinfo:
        element_space(_element("magic", 0, 0))
    assert "magic" in str(excinfo.value)
    # it is a ValueError, so callers that already catch that keep working
    assert isinstance(excinfo.value, ValueError)


def test_a_cdp_element_without_a_viewport_origin_raises():
    """No viewport origin: fail, never fall back to (0, 0) and click (10, 20)."""
    with pytest.raises(MissingViewportOrigin):
        to_screen_point(_element("cdp", 10, 20), window_origin=(300, 200))


def test_a_window_element_without_a_window_origin_raises():
    with pytest.raises(MissingWindowOrigin):
        to_screen_point(_element("uia", 10, 20))


def test_the_viewport_space_ignores_the_window_origin():
    point = to_screen_xy("viewport", 10, 20, window_origin=(300, 200),
                         viewport_origin=(100, 100))
    assert point == (110, 120)


def test_an_unknown_space_name_raises_in_the_primitive():
    with pytest.raises(UnknownCoordinateSpace):
        to_screen_xy("screenish", 1, 2, window_origin=(0, 0))
