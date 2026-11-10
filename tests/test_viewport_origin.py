"""The viewport origin: converted when measured, refused when not.

``Element.center`` from a browser backend is viewport-relative. Turning it
into a physical click needs the viewport's top-left corner in screen
pixels, and WebDriver BiDi never states it: ``browser.getClientWindows``
gives the *window* (chrome included), and the offset between that window
and the page viewport is not exposed. These tests pin both halves of the
answer - the Gecko measurement that is exact, and the refusal that keeps
an unmeasured corner from becoming a silently shifted click.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from openeyes.backends import bidi, cdp
from openeyes.backends import browser as browser_api
from openeyes.backends.bidi import BidiConnection
from openeyes.core.coords import MissingViewportOrigin, to_screen_point
from openeyes.core.schema import BBox, Center, Element
from openeyes.core.viewport import (
    SOURCE_CALLER,
    ViewportOrigin,
    from_css_point,
    origin_from_probe,
)

#: Measured on Firefox 159 / Windows, 2240x1400 at 130 % scaling, with the
#: window moved to (400, 260): the page reported (313.5667, 272.1667) CSS
#: pixels at dpr 1.3043478260869565, and Win32's ClientToScreen agreed at
#: (409, 260) plus 95 px of chrome. Nothing here is rounded by hand.
FIRST_INSTANCE_JS = {"x": 313.5666809082031, "y": 272.1666564941406,
                     "dpr": 1.3043478260869565}
FIRST_INSTANCE_SCREEN = (409, 355)


def _element(x: int = 10, y: int = 20, backend: str = "bidi") -> Element:
    return Element(backend=backend, control_type="Button", name="Save",
                   bbox=BBox(x - 5, y - 5, 10, 10), center=Center(x, y),
                   automation_id="SaveButton")


def _conn(payload, *, client_windows=None) -> BidiConnection:
    conn = BidiConnection.__new__(BidiConnection)
    conn.context_id = "tab-b"
    conn.contexts = [{"context": "tab-b", "url": "https://target",
                      "parent": None, "clientWindow": "win-1"}]
    conn.evaluate = MagicMock(return_value=payload)
    conn.call = MagicMock(return_value={"clientWindows": client_windows or []})
    conn._elements = []
    return conn


# --------------------------------------------------------------- the maths
def test_css_pixels_times_dpr_are_the_measured_screen_point():
    """The number the browser reported, multiplied - not nudged to fit."""
    assert from_css_point(**{"css_x": FIRST_INSTANCE_JS["x"],
                             "css_y": FIRST_INSTANCE_JS["y"],
                             "dpr": FIRST_INSTANCE_JS["dpr"]}).as_tuple() \
        == FIRST_INSTANCE_SCREEN


def test_the_conversion_keeps_the_numbers_it_used():
    origin = from_css_point(313.5667, 272.1667, 1.3043478260869565)

    assert origin.css_x == pytest.approx(313.5667)
    assert origin.dpr == pytest.approx(1.3043478260869565)
    assert "313.5667" in origin.describe()


def test_a_zero_corner_is_a_corner_not_a_missing_value():
    """A maximised window at the screen's top-left really is (0, 0)."""
    origin = from_css_point(0, 0, 1.0, source=SOURCE_CALLER)

    assert origin.as_tuple() == (0, 0)


@pytest.mark.parametrize("value", [None, float("nan"), float("inf"), "left"])
def test_a_corner_that_was_not_measured_raises(value):
    with pytest.raises(MissingViewportOrigin):
        from_css_point(value, 10, 1.0)


def test_a_nonsense_device_pixel_ratio_raises():
    with pytest.raises(ValueError):
        from_css_point(10, 20, 0)
    with pytest.raises(ValueError):
        from_css_point(10, 20, -1.5)


# ------------------------------------------------------------ chrome answers
def test_the_probe_answer_becomes_an_origin():
    origin = origin_from_probe(FIRST_INSTANCE_JS)

    assert origin.as_tuple() == FIRST_INSTANCE_SCREEN
    assert "mozInnerScreenX" in origin.source


def test_a_browser_without_moz_inner_screen_refuses():
    """Chrome answers null: that is an absence, not a zero."""
    with pytest.raises(MissingViewportOrigin) as excinfo:
        origin_from_probe({"x": None, "y": None, "dpr": 1.0})

    assert "mozInnerScreenX" in str(excinfo.value)


def test_a_probe_without_a_device_pixel_ratio_refuses():
    with pytest.raises(MissingViewportOrigin):
        origin_from_probe({"x": 10, "y": 20, "dpr": None})


def test_a_probe_that_is_not_a_mapping_refuses():
    with pytest.raises(MissingViewportOrigin):
        origin_from_probe("313.5,272.1")


# ------------------------------------------------------------- the BiDi seam
def test_bidi_reads_the_viewport_origin_from_the_page():
    conn = _conn(FIRST_INSTANCE_JS)

    origin = conn.viewport_origin()

    assert origin.as_tuple() == FIRST_INSTANCE_SCREEN
    assert conn.evaluate.call_args.kwargs["context_id"] == "tab-b"
    assert "mozInnerScreenX" in conn.evaluate.call_args.args[0]


def test_a_missing_viewport_origin_names_the_window_it_does_know():
    conn = _conn({"x": None, "y": None, "dpr": 1.0}, client_windows=[{
        "clientWindow": "win-1", "active": True, "state": "normal",
        "x": 307, "y": 199, "width": 843, "height": 613,
    }])

    with pytest.raises(MissingViewportOrigin) as excinfo:
        conn.viewport_origin()

    message = str(excinfo.value)
    # what it tried, and what it did get
    assert "mozInnerScreenX" in message
    assert "client window" in message
    assert "x=307" in message and "height=613" in message
    # why the window is not the answer
    assert "chrome included" in message
    # and the two ways forward
    assert "viewport_origin=(x, y)" in message
    assert "origin='viewport'" in message
    # never a default
    assert "Do not substitute the window origin or (0, 0)" in message


def test_a_script_error_becomes_a_missing_origin_with_the_cause():
    conn = _conn(None)
    conn.evaluate.side_effect = bidi.BidiError("script.evaluate failed: nope")

    with pytest.raises(MissingViewportOrigin) as excinfo:
        conn.viewport_origin()

    assert "script.evaluate failed: nope" in str(excinfo.value)


def test_the_client_window_is_matched_by_id_then_by_first():
    windows = [
        {"clientWindow": "win-2", "x": 1, "y": 2, "width": 3, "height": 4},
        {"clientWindow": "win-1", "x": 5, "y": 6, "width": 7, "height": 8},
    ]
    assert _conn({}, client_windows=windows).client_window()["x"] == 5

    conn = _conn({}, client_windows=windows)
    conn.contexts = [{"context": "tab-b", "url": "", "parent": None,
                      "clientWindow": ""}]
    assert conn.client_window()["x"] == 1


def test_a_browser_without_get_client_windows_is_not_an_error_yet():
    """Diagnostics are best-effort; the refusal still happens."""
    conn = _conn({"x": None, "y": None, "dpr": 1.0})
    conn.call.side_effect = bidi.BidiError("browser.getClientWindows: unknown")

    assert conn.client_window() is None
    with pytest.raises(MissingViewportOrigin):
        conn.viewport_origin()


# ------------------------------------------------------- screen point, once
def test_a_bidi_element_on_screen_is_its_centre_plus_the_origin():
    conn = _conn(FIRST_INSTANCE_JS)

    assert bidi.screen_point(conn, _element(10, 20)) == (419, 375)


def test_a_caller_supplied_origin_is_used_verbatim():
    conn = _conn({"x": None, "y": None, "dpr": 1.0})

    assert bidi.screen_point(
        conn, _element(10, 20),
        origin=ViewportOrigin(300, 200, 300, 200, 1.0, SOURCE_CALLER),
    ) == (310, 220)


def test_a_missing_origin_never_becomes_a_zero_click():
    """No origin: the conversion raises instead of clicking at the DOM point."""
    conn = _conn({"x": None, "y": None, "dpr": 1.0})

    with pytest.raises(MissingViewportOrigin):
        bidi.screen_point(conn, _element(10, 20))


def test_the_in_page_click_path_still_needs_no_screen_origin():
    """``click_center`` dispatches with origin=viewport; it must stay that way."""
    conn = MagicMock()
    conn.context_of.return_value = "tab-b"

    bidi.click_center(conn, _element(10, 20))

    conn.click_at.assert_called_once_with(10, 20, context_id="tab-b")
    conn.viewport_origin.assert_not_called()


# ---------------------------------------------------------- protocol switch
def test_the_protocol_switch_delegates_the_bidi_origin():
    conn = _conn(FIRST_INSTANCE_JS)

    assert browser_api.viewport_origin("bidi", conn).as_tuple() \
        == FIRST_INSTANCE_SCREEN
    assert browser_api.screen_point("bidi", conn, _element(10, 20)) \
        == (419, 375)


def test_the_protocol_switch_refuses_a_backend_without_a_source(monkeypatch):
    """CDP dispatches in-page; it has no viewport-origin source to offer."""
    import types

    stub = types.SimpleNamespace()   # like cdp: no viewport_origin at all
    monkeypatch.setattr(browser_api, "backend_for", lambda protocol: stub)

    with pytest.raises(MissingViewportOrigin) as excinfo:
        browser_api.viewport_origin("cdp", MagicMock())

    assert "cdp" in str(excinfo.value)


def test_the_window_origin_is_still_not_a_viewport_origin():
    """The old mistake, pinned: a window origin must not satisfy this."""
    with pytest.raises(MissingViewportOrigin):
        to_screen_point(_element(10, 20), window_origin=(300, 200))


# ------------------------------------------------ CDP (Chromium) refuses too
#: Measured on Edge 152 / Windows 11 25H2, 2240x1400 at 130 %: the window sat
#: at (130,104) in screen device pixels and the page's own (0,0) marker was
#: found at (145,209) inside the captured frame - 15 px right, 105 px down.
CDP_WINDOW = {"screenX": 100, "screenY": 80,
              "outerWidth": 702, "outerHeight": 522,
              "innerWidth": 680, "innerHeight": 430,
              "dpr": 1.3020833730697632, "fullscreen": False,
              "displayModeFullscreen": False}

#: The same page in Browser.setWindowBounds fullscreen: window edge and
#: viewport edge did coincide at (0,0), but Chromium still answered
#: outerHeight=1076 against innerHeight=1077.
CDP_FULLSCREEN = {"screenX": 0, "screenY": 0,
                  "outerWidth": 1721, "outerHeight": 1076,
                  "innerWidth": 1721, "innerHeight": 1077,
                  "dpr": 1.3020833730697632, "fullscreen": True,
                  "displayModeFullscreen": True}


def _cdp(report=None, *, bounds=None, error=None) -> MagicMock:
    """A CDP connection that answers with an injected payload."""
    conn = MagicMock()
    conn.evaluate = MagicMock(return_value=report)
    if error is not None:
        conn.evaluate.side_effect = cdp.CDPError(error)
    if bounds is None:
        conn.call = MagicMock(side_effect=cdp.CDPError(
            "Browser.getWindowForTarget: Browser window not found"))
    else:
        conn.call = MagicMock(side_effect=[
            {"windowId": 7}, {"bounds": bounds}])
    return conn


def test_cdp_never_returns_a_coordinate_for_a_window_off_the_origin():
    """The window's position is not the viewport's position."""
    with pytest.raises(MissingViewportOrigin) as excinfo:
        cdp.viewport_origin(_cdp(CDP_WINDOW))

    message = str(excinfo.value)
    # what it asked, and what the page answered
    assert "screenX=100" in message and "innerWidth=680" in message
    # the measured gap, not a guess at it
    assert "(130,104)" in message and "(145,209)" in message
    assert "105 px" in message and "120 device px" in message
    # and the two ways forward
    assert "viewport_origin=(x, y)" in message
    assert "Input.dispatchMouseEvent" in message
    assert "Do not substitute the window origin or (0, 0)" in message


def test_cdp_refuses_in_fullscreen_even_though_the_edges_met():
    """Fullscreen is not a certificate: no field says 'no chrome'."""
    with pytest.raises(MissingViewportOrigin) as excinfo:
        cdp.viewport_origin(_cdp(CDP_FULLSCREEN, bounds={
            "windowState": "fullscreen", "left": 0, "top": 0,
            "width": 1721, "height": 1076}))

    message = str(excinfo.value)
    assert "outerHeight=1076" in message and "innerHeight=1077" in message
    assert "fullscreen is not a certificate" in message
    assert "windowState=fullscreen" in message


def test_cdp_refusal_names_the_route_that_needs_no_screen_origin():
    """A windowed refusal must say what to do instead, not just 'no'."""
    with pytest.raises(MissingViewportOrigin) as excinfo:
        cdp.viewport_origin(_cdp(CDP_WINDOW))

    message = str(excinfo.value)
    assert "chrome band" in message
    assert "dispatch the click in the page" in message
    assert "viewport_relative" not in message


def test_cdp_refuses_even_when_every_number_looks_perfect():
    """outer == inner at (0,0) is still an inference, so there is no return."""
    perfect = {"screenX": 0, "screenY": 0, "outerWidth": 1280,
               "outerHeight": 720, "innerWidth": 1280, "innerHeight": 720,
               "dpr": 1.0, "fullscreen": True}

    with pytest.raises(MissingViewportOrigin):
        cdp.viewport_origin(_cdp(perfect))


def test_cdp_reports_a_probe_failure_as_the_reason():
    conn = _cdp(error="Runtime.evaluate failed: target closed")

    with pytest.raises(MissingViewportOrigin) as excinfo:
        cdp.viewport_origin(conn)

    assert "Runtime.evaluate failed: target closed" in str(excinfo.value)


def test_cdp_survives_a_page_session_without_browser_window_access():
    """getWindowForTarget is refused on a page session; refusal still stands."""
    conn = _cdp(CDP_WINDOW)

    with pytest.raises(MissingViewportOrigin) as excinfo:
        cdp.viewport_origin(conn)

    assert "Browser.getWindowBounds reported" not in str(excinfo.value)
    assert "screenX=100" in str(excinfo.value)


def test_the_protocol_switch_delegates_the_cdp_refusal():
    with pytest.raises(MissingViewportOrigin) as excinfo:
        browser_api.viewport_origin("cdp", _cdp(CDP_WINDOW))

    assert "cdp (Chromium)" in str(excinfo.value)


def test_the_cdp_click_path_needs_no_screen_origin():
    """``click_center`` dispatches inside the page and must stay that way."""
    conn = MagicMock()

    cdp.click_center(conn, _element(10, 20, backend="cdp"))

    sent = [c.args[0] for c in conn.call.call_args_list]
    assert sent == ["Input.dispatchMouseEvent", "Input.dispatchMouseEvent"]
    conn.evaluate.assert_not_called()
