"""Cross-platform mouse / keyboard input facade.

Default impl is Windows (Win32). macOS / Linux implementations planned.
"""
from __future__ import annotations
import sys


def _backend():
    if sys.platform.startswith("win"):
        from openeyes.actuators import win32 as backend
    elif sys.platform == "darwin":
        from openeyes.actuators import cg as backend  # type: ignore
    elif sys.platform.startswith("linux"):
        from openeyes.actuators import xtest as backend  # type: ignore
    else:
        raise NotImplementedError(f"unsupported platform: {sys.platform}")
    return backend


def click_xy(x: int, y: int, button: str = "left", double: bool = False) -> None:
    """Click at absolute screen coords."""
    _backend().click_xy(x, y, button=button, double=double)


def send_hotkey(*keys: str) -> None:
    """Press a chord. e.g. send_hotkey('ctrl', 'a')."""
    _backend().send_hotkey(*keys)


def type_text(text: str, interval: float = 0.0) -> None:
    """Type a literal string."""
    _backend().type_text(text, interval=interval)


def drag(from_xy: tuple[int, int], to_xy: tuple[int, int],
         duration_ms: int = 200) -> None:
    """Click-drag from one point to another."""
    _backend().drag(from_xy, to_xy, duration_ms=duration_ms)


def scroll(dx: int, dy: int, at_xy: tuple[int, int] | None = None) -> None:
    """Scroll wheel. dy positive = scroll down, dx positive = scroll right."""
    _backend().scroll(dx, dy, at_xy=at_xy)