"""Coordinate spaces, written down once instead of guessed per caller.

Three backends report the same thing — the centre of an element — in three
different spaces:

============  ===========  ==============================================
``backend``   space        ``(0, 0)`` means
============  ===========  ==============================================
``uia``       ``window``   the top-left corner of the window
``vision``    ``window``   the top-left corner of the window
``cdp``       ``viewport`` the top-left corner of the page viewport
============  ===========  ==============================================

:func:`openeyes.core.actuator.click_xy` takes **absolute screen**
coordinates. An element measured in a window-relative space therefore has
the window origin added back before it is clicked; an element measured in
the viewport space has the viewport origin added. Skipping that step does
not fail loudly — it shifts every physical click by one window origin, so
a window at (300, 200) makes a "successful" click miss by (300, 200).

The module is deliberately pure: no ctypes, no win32, no pywinauto, no
PIL. It runs on the Linux CI, and it is the one place the conversion is
written down, so the CLI, the backends and the MCP server cannot each
invent their own version of it.
"""
from __future__ import annotations

#: Element coordinates are relative to the window's top-left corner.
WINDOW = "window"

#: Element coordinates are relative to the page viewport's top-left corner.
VIEWPORT = "viewport"

#: Which space each backend reports. A backend that is not listed is not
#: guessed at — :func:`element_space` raises instead.
BACKEND_SPACES: dict[str, str] = {
    "uia": WINDOW,
    "vision": WINDOW,
    "cdp": VIEWPORT,
}


class UnknownCoordinateSpace(ValueError):
    """No coordinate space is known for this backend.

    Raised rather than assumed: guessing the space is how a click ends up
    one window origin away from the control it named.
    """


class MissingOrigin(ValueError):
    """A required origin was not supplied.

    An origin is never defaulted to ``(0, 0)``: that silently turns a
    window-relative point into a screen point and hides the mistake.
    """


class MissingWindowOrigin(MissingOrigin):
    """A window-relative element was converted without a window origin."""


class MissingViewportOrigin(MissingOrigin):
    """A viewport-relative element was converted without a viewport origin."""


def element_space(element: object) -> str:
    """The coordinate space *element* was measured in.

    ``"window"`` for the uia / vision backends, ``"viewport"`` for cdp.
    An unknown backend raises :class:`UnknownCoordinateSpace` — a caller
    that does not know the space must not click on a guess.
    """
    backend = str(getattr(element, "backend", "") or "").strip().lower()
    space = BACKEND_SPACES.get(backend)
    if space is None:
        known = ", ".join(sorted(BACKEND_SPACES))
        raise UnknownCoordinateSpace(
            f"no coordinate space is known for backend {backend!r}; "
            f"known backends: {known}"
        )
    return space


def _origin(value: object, name: str, missing: type[MissingOrigin]) -> tuple[int, int]:
    """Validate an origin pair, or say which one is missing."""
    if value is None:
        raise missing(f"{name} is required to reach screen coordinates")
    try:
        ox, oy = value  # type: ignore[misc]
    except (TypeError, ValueError):
        raise ValueError(
            f"{name} must be an (x, y) pair, got {value!r}"
        ) from None
    return int(ox), int(oy)


def to_screen_xy(space: str, x: int, y: int, *, window_origin=None,
                 viewport_origin=None) -> tuple[int, int]:
    """Map one point from *space* to absolute screen pixels.

    ``window`` space adds ``window_origin``; ``viewport`` space adds
    ``viewport_origin`` and refuses to run without one.
    """
    if space == WINDOW:
        ox, oy = _origin(window_origin, "window_origin", MissingWindowOrigin)
        return int(x) + ox, int(y) + oy
    if space == VIEWPORT:
        ox, oy = _origin(viewport_origin, "viewport_origin", MissingViewportOrigin)
        return int(x) + ox, int(y) + oy
    raise UnknownCoordinateSpace(
        f"unknown coordinate space {space!r}; expected {WINDOW!r} or {VIEWPORT!r}"
    )


def to_screen_point(element: object, *, window_origin=None,
                    viewport_origin=None) -> tuple[int, int]:
    """The absolute screen point to click for *element*.

    ``window_origin`` is the window's top-left corner on screen (from
    ``GetWindowRect``); ``viewport_origin`` is the viewport's top-left
    corner on screen. Pass both when both are known — at most one is
    used, and ``None`` for the one that does not apply.
    """
    center = getattr(element, "center", None)
    x = getattr(center, "x", None)
    y = getattr(center, "y", None)
    if x is None or y is None:
        try:
            x, y = center  # type: ignore[misc]
        except (TypeError, ValueError):
            raise ValueError(
                f"{element!r} has no centre to convert"
            ) from None
    return to_screen_xy(element_space(element), x, y,
                        window_origin=window_origin,
                        viewport_origin=viewport_origin)
