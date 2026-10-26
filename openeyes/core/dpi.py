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

PER_MONITOR_AWARE_V2 = "per-monitor-v2"
PER_MONITOR_AWARE = "per-monitor"
SYSTEM_AWARE = "system"
UNAWARE = "unaware"

# DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 is the pseudo-handle -4.
_CONTEXT_PER_MONITOR_AWARE_V2 = -4
# PROCESS_PER_MONITOR_DPI_AWARE
_PROCESS_PER_MONITOR_DPI_AWARE = 2

_AWARENESS_BY_ID = {0: UNAWARE, 1: SYSTEM_AWARE, 2: PER_MONITOR_AWARE}

_MODE: str | None = None


def current_dpi_awareness() -> str:
    """Return the awareness level of the current process/thread."""
    if not sys.platform.startswith("win"):
        return SYSTEM_AWARE
    user32 = ctypes.windll.user32
    try:
        ctx = user32.GetThreadDpiAwarenessContext()
    except Exception:
        return UNAWARE
    value = ctypes.cast(ctx, ctypes.c_void_p).value
    if value == _CONTEXT_PER_MONITOR_AWARE_V2:
        return PER_MONITOR_AWARE_V2
    try:
        awareness = ctypes.c_int()
        user32.GetAwarenessFromDpiAwarenessContext(ctx, ctypes.byref(awareness))
        return _AWARENESS_BY_ID.get(awareness.value, UNAWARE)
    except Exception:
        return UNAWARE


def ensure_dpi_awareness(force: bool = False) -> str:
    """Make the process per-monitor DPI aware. Returns the resulting mode.

    Idempotent: the mode is detected and cached. A process that is already
    aware keeps its own level unless ``force`` is set — we only ever
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

    user32 = ctypes.windll.user32
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
