"""One entry point for the structured browser backends.

CDP (Chromium / Edge / Lightpanda) and WebDriver BiDi (Firefox Remote
Agent) expose the same verbs to OpenEyes. This module picks the backend by
protocol so the CLI, the MCP server and the resolve chain only ever name a
protocol, never a transport.
"""
from __future__ import annotations

from typing import Any

from openeyes.core.coords import MissingViewportOrigin, to_screen_point

PROTOCOLS = ("cdp", "bidi")
DEFAULT_PROTOCOL = "cdp"


class ProtocolError(ValueError):
    """Raised when an unknown browser protocol is requested."""


def backend_for(protocol: str = DEFAULT_PROTOCOL):
    """Return the backend module implementing ``protocol``."""
    name = (protocol or DEFAULT_PROTOCOL).strip().lower()
    if name == "cdp":
        from openeyes.backends import cdp
        return cdp
    if name in ("bidi", "webdriverbidi", "webdriver-bidi"):
        from openeyes.backends import bidi
        return bidi
    raise ProtocolError(
        "unknown browser protocol " + repr(protocol)
        + "; expected one of " + ", ".join(PROTOCOLS)
    )


def connect(protocol: str = DEFAULT_PROTOCOL, **kwargs: Any):
    """Open a connection with the requested backend."""
    return backend_for(protocol).connect(**kwargs)


def list_tabs(protocol: str = DEFAULT_PROTOCOL, **kwargs: Any) -> list:
    return backend_for(protocol).list_tabs(**kwargs)


def scan_dom(protocol: str = DEFAULT_PROTOCOL, conn: Any = None, **kwargs: Any) -> list:
    return backend_for(protocol).scan_dom(conn, **kwargs)


def click_center(protocol: str = DEFAULT_PROTOCOL, conn: Any = None,
                 element: Any = None) -> None:
    backend_for(protocol).click_center(conn, element)


def type_text(protocol: str = DEFAULT_PROTOCOL, conn: Any = None,
              text: str = "", element: Any = None,
              press_enter: bool = False) -> None:
    backend_for(protocol).type_text(conn, text, element=element,
                                    press_enter=press_enter)


def screenshot(protocol: str = DEFAULT_PROTOCOL, conn: Any = None,
               out_path: str = "") -> str:
    return backend_for(protocol).screenshot(conn, out_path)


#: Backends that can measure where the page viewport sits on screen. A
#: backend that cannot is not guessed at: its click path stays in-page
#: (CDP ``Input.dispatchMouseEvent``, BiDi ``origin: "viewport"``), and a
#: caller that needs a physical click must supply the origin itself.
VIEWPORT_ORIGIN_BACKENDS = ("bidi",)


def viewport_origin(protocol: str = DEFAULT_PROTOCOL, conn: Any = None,
                    element: Any = None):
    """The viewport's screen corner for *protocol*, or a stated refusal."""
    backend = backend_for(protocol)
    resolver = getattr(backend, "viewport_origin", None)
    if resolver is None:
        raise MissingViewportOrigin(
            f"the {protocol!r} backend has no source for the page viewport's "
            "screen corner; its click path dispatches inside the page "
            "(no screen coordinates involved). Pass viewport_origin=(x, y) "
            "from a source that measured it, or click through the backend."
        )
    return resolver(conn, element)


def screen_point(protocol: str = DEFAULT_PROTOCOL, conn: Any = None,
                 element: Any = None, *, origin=None) -> tuple[int, int]:
    """Absolute screen pixels for *element*, using a measured origin."""
    if origin is None:
        origin = viewport_origin(protocol, conn, element)
    pair = origin.as_tuple() if hasattr(origin, "as_tuple") else origin
    return to_screen_point(element, viewport_origin=pair)
