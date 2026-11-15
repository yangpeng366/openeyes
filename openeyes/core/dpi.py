"""DPI awareness helpers.

Windows virtualises coordinates for DPI-unaware processes. UI Automation
reports *physical* screen pixels, but a DPI-unaware caller sees them scaled
and ``GetWindowRect`` / ``SetCursorPos`` stop agreeing with each other —
which is exactly how a click ends up a few pixels off, or on the wrong
element entirely.

:func:`ensure_dpi_awareness` puts the process into per-monitor-aware mode
once, so every coordinate in the process is a real screen pixel. It is
idempotent and never downgrades an awareness level the host already set.
"""
from __future__ import annotations

import ctypes
import sys
from typing import Any

PER_MONITOR_AWARE_V2 = "per-monitor-v2"
PER_MONITOR_AWARE = "per-monitor"
SYSTEM_AWARE = "system"
UNAWARE = "unaware"

# DPI_AWARENESS_CONTEXT pseudo-handles (winuser.h).  They are pointer-sized
# negative values, so they only compare equal to the API's own handles when
# the value is kept full width.
_CONTEXT_UNAWARE = -1
_CONTEXT_SYSTEM_AWARE = -2
_CONTEXT_PER_MONITOR_AWARE = -3
_CONTEXT_PER_MONITOR_AWARE_V2 = -4

#: Ordered most aware first; the first context the thread matches names it.
_CONTEXTS: tuple[tuple[int, str], ...] = (
    (_CONTEXT_PER_MONITOR_AWARE_V2, PER_MONITOR_AWARE_V2),
    (_CONTEXT_PER_MONITOR_AWARE, PER_MONITOR_AWARE),
    (_CONTEXT_SYSTEM_AWARE, SYSTEM_AWARE),
    (_CONTEXT_UNAWARE, UNAWARE),
)

# PROCESS_PER_MONITOR_DPI_AWARE
_PROCESS_PER_MONITOR_DPI_AWARE = 2

_MODE: str | None = None
_PROTOTYPES_SET = False


def _handle(value: int) -> int:
    """The pointer-sized value of a DPI awareness pseudo-handle (-1 .. -5)."""
    return ctypes.c_void_p(value).value


def _user32() -> Any:
    """``user32`` with the DPI functions' real prototypes declared.

    ctypes defaults an undeclared return type to ``c_int`` and an undeclared
    argument to a 32-bit ``int``.  A ``DPI_AWARENESS_CONTEXT`` is a
    *pointer*-sized handle, so an undeclared prototype truncates it to 32
    bits: the predefined values survive by luck, a real handle does not.
    Declaring the signatures keeps the handle whole in both directions.
    """
    global _PROTOTYPES_SET
    user32 = ctypes.windll.user32
    if _PROTOTYPES_SET:
        return user32
    try:
        user32.GetThreadDpiAwarenessContext.argtypes = []
        user32.GetThreadDpiAwarenessContext.restype = ctypes.c_void_p
        user32.AreDpiAwarenessContextsEqual.argtypes = [ctypes.c_void_p,
                                                        ctypes.c_void_p]
        user32.AreDpiAwarenessContextsEqual.restype = ctypes.c_int
        user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        user32.SetProcessDpiAwarenessContext.restype = ctypes.c_int
    except Exception:
        pass
    _PROTOTYPES_SET = True
    return user32


def current_dpi_awareness() -> str:
    """Return the awareness level of the current process/thread.

    The level is read by comparing the live thread context with the API's
    own predefined contexts (``AreDpiAwarenessContextsEqual``) instead of
    decoding ``GetAwarenessFromDpiAwarenessContext``'s out parameter: the
    comparison needs no guess about which integer an out parameter may hold,
    and it is the documented way to ask "which context is this?".
    """
    if not sys.platform.startswith("win"):
        return SYSTEM_AWARE
    user32 = _user32()
    try:
        ctx = user32.GetThreadDpiAwarenessContext()
    except Exception:
        return UNAWARE
    if not ctx:
        return UNAWARE
    try:
        for value, name in _CONTEXTS:
            if user32.AreDpiAwarenessContextsEqual(
                    ctypes.c_void_p(int(ctx)), ctypes.c_void_p(_handle(value))):
                return name
    except Exception:
        return UNAWARE
    return UNAWARE


def ensure_dpi_awareness(force: bool = False) -> str:
    """Make the process per-monitor DPI aware. Returns the resulting mode.

    Idempotent: the mode is detected and cached. A process that is already
    aware keeps its own level unless ``force`` is set - we only ever
    *upgrade*, never downgrade what the host application asked for.
    """
    global _MODE
    if _MODE is not None and not force:
        return _MODE
    if not sys.platform.startswith("win"):
        _MODE = SYSTEM_AWARE
        return _MODE

    current = current_dpi_awareness()
    if current != UNAWARE and not force:
        _MODE = current
        return _MODE

    user32 = _user32()
    try:
        if user32.SetProcessDpiAwarenessContext(
                ctypes.c_void_p(_CONTEXT_PER_MONITOR_AWARE_V2)):
            _MODE = PER_MONITOR_AWARE_V2
            return _MODE
    except Exception:
        pass
    try:
        hr = ctypes.windll.shcore.SetProcessDpiAwareness(
            _PROCESS_PER_MONITOR_DPI_AWARE)
        if hr == 0:
            _MODE = PER_MONITOR_AWARE
            return _MODE
    except Exception:
        pass
    try:
        if user32.SetProcessDPIAware():
            _MODE = SYSTEM_AWARE
            return _MODE
    except Exception:
        pass
    _MODE = current_dpi_awareness()
    return _MODE
