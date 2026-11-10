"""Where the page viewport sits on screen - and what to do when nobody says.

``openeyes.core.coords`` knows that a DOM box is measured in *viewport*
space and that a physical click needs the viewport's top-left corner in
screen pixels. This module is where that corner comes from, and it
writes down the three kinds of answer instead of blurring them:

==============  ==================================================  ============
tier            source                                              status
==============  ==================================================  ============
spec-guaranteed ``browser.getClientWindows``: ``x``/``y`` in CSS     not enough
                pixels from the web-exposed screen area to the
                *client window* - the whole window, chrome included
measured        Firefox ``window.mozInnerScreenX`` /                 exact, Gecko
                ``window.mozInnerScreenY`` (CSS pixels) times        only
                ``window.devicePixelRatio``
estimate        ``outerHeight - innerHeight`` as a chrome height,    refused
                or "the window is probably at (0, 0)"
==============  ==================================================  ============

The spec tier is a real fact and still not the answer: BiDi says where
the *window* is, and never says how much of that window is browser chrome
(tab strip, toolbars, borders). Subtracting a guessed chrome height is
the estimate tier, and an estimate that is 90 px wrong is a click that
lands on the page heading instead of the button - silently, with a
success response. So this module converts what was actually measured and
raises :class:`~openeyes.core.coords.MissingViewportOrigin` otherwise.

Measured on Firefox 159 / Windows 11 25H2, 2240x1400 at 130 % scaling
(``devicePixelRatio`` 1.3043478260869565), window moved to (400, 260)::

    mozInnerScreenX/Y  313.5667, 272.1667   (CSS px)
    x dpr              409.0,    355.0      (device px)
    Win32 client origin 409, 260   + chrome 95 px

The multiplication matched ``ClientToScreen`` to the pixel, which is why
it is the one conversion written here. ``mozInnerScreenX`` is a Gecko
extension rather than a WebDriver BiDi command, so a browser that does
not answer it gets a refusal, not a fallback.

The module is pure: it takes numbers, it returns numbers, and it never
imports a backend. The backend is the thing being audited.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from openeyes.core.coords import MissingViewportOrigin

#: Firefox's viewport corner, in CSS pixels, times ``devicePixelRatio``.
SOURCE_INNER_SCREEN = "mozInnerScreenX/Y * devicePixelRatio"

#: The caller passed the corner itself (screen / device pixels).
SOURCE_CALLER = "caller-supplied viewport_origin"

#: A corner this module converted from CSS pixels.
SOURCE_CSS = "css-pixels * devicePixelRatio"


@dataclass(frozen=True)
class ViewportOrigin:
    """The screen point of the viewport's top-left corner, in device pixels.

    ``css_x``/``css_y``/``dpr`` are kept next to the result so a report can
    show *why* the click went where it did: the browser said 313.5667 CSS
    pixels at a ratio of 1.3043, and the pointer was sent to 409.
    """

    x: int
    y: int
    css_x: float
    css_y: float
    dpr: float
    source: str = SOURCE_INNER_SCREEN

    def as_tuple(self) -> tuple[int, int]:
        """The pair :func:`openeyes.core.coords.to_screen_point` wants."""
        return self.x, self.y

    def to_dict(self) -> dict[str, Any]:
        return {
            "x": self.x,
            "y": self.y,
            "css_x": self.css_x,
            "css_y": self.css_y,
            "dpr": self.dpr,
            "source": self.source,
        }

    def describe(self) -> str:
        return (f"viewport origin ({self.x},{self.y}) device px "
                f"= ({self.css_x:.4f},{self.css_y:.4f}) CSS px "
                f"x dpr {self.dpr:.6f} [{self.source}]")


def _finite(value: object, name: str) -> float:
    """A real number, or a refusal - never ``None`` coerced to 0."""
    if value is None or isinstance(value, bool):
        raise MissingViewportOrigin(f"{name} was not reported")
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise MissingViewportOrigin(
            f"{name} was not a number: {value!r}"
        ) from None
    if not math.isfinite(number):
        raise MissingViewportOrigin(f"{name} was not finite: {value!r}")
    return number


def from_css_point(css_x: object, css_y: object, dpr: object, *,
                   source: str = SOURCE_CSS) -> ViewportOrigin:
    """Convert a viewport corner from CSS pixels to screen device pixels.

    Zero is a legitimate corner (a maximised window at the screen's
    top-left), so ``(0, 0)`` is returned as ``(0, 0)``. Only *missing*
    numbers raise: the difference between "the corner is zero" and "the
    browser did not say" is the whole point of the contract.
    """
    x = _finite(css_x, "viewport css x")
    y = _finite(css_y, "viewport css y")
    ratio = _finite(dpr, "devicePixelRatio")
    if ratio <= 0:
        raise ValueError(
            f"devicePixelRatio must be positive, got {ratio!r}"
        )
    return ViewportOrigin(
        x=int(round(x * ratio)),
        y=int(round(y * ratio)),
        css_x=x,
        css_y=y,
        dpr=ratio,
        source=source,
    )


def origin_from_probe(payload: object, *,
                      source: str = SOURCE_INNER_SCREEN) -> ViewportOrigin:
    """Read the browser's JS probe answer, or refuse with the reason.

    The probe returns ``{"x": ..., "y": ..., "dpr": ...}`` where a browser
    without the Gecko property answers ``null`` for the first two. A
    browser that refuses the whole ``script.evaluate`` never reaches this
    function; the caller turns its exception into
    :func:`missing_origin_message`.
    """
    if not isinstance(payload, Mapping):
        raise MissingViewportOrigin(
            f"the viewport probe returned {type(payload).__name__}, "
            "not an {x, y, dpr} mapping"
        )
    x = payload.get("x")
    y = payload.get("y")
    dpr = payload.get("dpr")
    missing = [name for name, value in (("x", x), ("y", y))
               if value is None]
    if missing:
        raise MissingViewportOrigin(
            "the page did not expose its viewport corner ("
            + ", ".join(missing) + " missing); mozInnerScreenX/Y are a "
            "Gecko extension and this browser did not answer them"
        )
    if dpr is None:
        raise MissingViewportOrigin(
            "the page did not expose devicePixelRatio, so CSS pixels "
            "cannot be turned into screen pixels"
        )
    return from_css_point(x, y, dpr, source=source)


def missing_origin_message(backend: str, *, detail: str = "",
                           client_window: Mapping[str, Any] | None = None
                           ) -> str:
    """The actionable sentence a caller sees instead of a shifted click.

    It names what was tried, what the browser *did* say (the window
    rectangle, when it answered), why that is not the viewport, and the
    two ways forward. It never suggests ``(0, 0)`` or the window origin as
    a stand-in: both are how a click ends up one chrome height away.
    """
    parts = [
        f"{backend} could not determine the page viewport's top-left corner "
        "in screen coordinates, so a viewport-relative point cannot be "
        "turned into a physical click"
    ]
    if detail:
        parts.append(detail)
    if client_window:
        where = ", ".join(
            f"{key}={client_window.get(key)}"
            for key in ("x", "y", "width", "height", "state")
            if client_window.get(key) is not None
        )
        if where:
            parts.append(
                "the browser did report the client window "
                f"({where}, CSS px per browser.getClientWindows), but that "
                "is the window, chrome included: WebDriver BiDi exposes no "
                "offset between the client window's edge and the viewport"
            )
    parts.append(
        "pass viewport_origin=(x, y) in screen/device pixels from a source "
        "that measured the viewport itself, or let the browser dispatch the "
        "click in the page (BiDi input.performActions with "
        "origin='viewport', which needs no screen origin)"
    )
    return "; ".join(parts) + ". Do not substitute the window origin or " \
        "(0, 0) for it."
