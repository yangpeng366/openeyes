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


def click_xy(x: int, y: int, button: str = "left", double: bool = False,
             press_ms: int = 30, settle_ms: int = 60) -> None:
    """Click at absolute screen coords."""
    backend = _backend()
    try:
        backend.click_xy(x, y, button=button, double=double,
                         press_ms=press_ms, settle_ms=settle_ms)
    except TypeError:
        # backends that do not support the fine-grained knobs yet
        backend.click_xy(x, y, button=button, double=double)


def move_to(x: int, y: int, settle_ms: int = 60, steps: int = 2) -> bool:
    """Move the cursor to (x, y) and let the target window see the motion."""
    return _backend().move_to(x, y, settle_ms=settle_ms, steps=steps)


def cursor_pos() -> tuple[int, int] | None:
    """Current cursor position, or None if it cannot be read."""
    return _backend().cursor_pos()


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