"""One entry point for the structured browser backends.

CDP (Chromium / Edge / Lightpanda) and WebDriver BiDi (Firefox Remote
Agent) expose the same verbs to OpenEyes. This module picks the backend by
protocol so the CLI, the MCP server and the resolve chain only ever name a
protocol, never a transport.
"""
from __future__ import annotations

from typing import Any

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
