"""OpenEyes - AI-friendly computer-use primitives.

Public API:

    from openeyes import Element, WindowInfo, list_windows, capture_window,
                        detect_elements, click_by_selector, click_xy,
                        send_hotkey, type_text

The default backend is platform-selected at import time:
    - Windows  -> openeyes.backends.uia + openeyes.actuators.win32
    - macOS    -> openeyes.backends.ax  + openeyes.actuators.cg    (planned)
    - Linux    -> openeyes.backends.atspi + openeyes.actuators.xtest (planned)
"""
from __future__ import annotations

from openeyes.core.actuator import click_xy, cursor_pos, drag, move_to, scroll, send_hotkey, type_text
from openeyes.core.capture import capture_screen, capture_window
from openeyes.core.dpi import current_dpi_awareness, ensure_dpi_awareness
from openeyes.core.lifecycle import (
    STATE_NOT_RUNNING,
    STATE_RUNNING,
    STATE_UNKNOWN,
    STATE_WINDOW_GONE,
    AppState,
    EnsureResult,
    app_state,
    ensure_app,
    launch_app,
    restore_window,
)
from openeyes.core.resolve import Attempt, ResolveResult, resolve_elements
from openeyes.core.schema import BBox, Center, Element, ElementState, WindowInfo
from openeyes.core.selector import click_by_selector, click_element, detect_elements, find_elements
from openeyes.core.supervise import Health, Round, SupervisionReport, supervise
from openeyes.core.verify import act_until, wait_until
from openeyes.core.windows import find_window, list_windows

# Lazy import for record submodule (optional dependency on openeyes itself)
# Usage: from openeyes.record import Recorder, Replayer, Trajectory

__version__ = "0.2.0"

__all__ = [
    "BBox", "Center", "Element", "ElementState", "WindowInfo",
    "list_windows", "find_window",
    "capture_window", "capture_screen",
    "detect_elements", "find_elements", "click_by_selector", "click_element",
    "click_xy", "send_hotkey", "type_text", "drag", "scroll",
    "move_to", "cursor_pos",
    "ensure_dpi_awareness", "current_dpi_awareness",
    "act_until", "wait_until",
    "app_state", "ensure_app", "launch_app", "restore_window",
    "supervise", "Health", "Round", "SupervisionReport",
    "AppState", "EnsureResult",
    "STATE_RUNNING", "STATE_WINDOW_GONE", "STATE_NOT_RUNNING", "STATE_UNKNOWN",
    "resolve_elements", "ResolveResult", "Attempt",
    "__version__",
]
