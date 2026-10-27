"""Core primitives — capture, detect, actuate."""
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
from openeyes.core.schema import BBox, Center, Element, ElementState, WindowInfo
from openeyes.core.selector import click_by_selector, click_element, detect_elements, find_elements
from openeyes.core.verify import act_until, wait_until
from openeyes.core.windows import find_window, list_windows

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
    "AppState", "EnsureResult",
    "STATE_RUNNING", "STATE_WINDOW_GONE", "STATE_NOT_RUNNING", "STATE_UNKNOWN",
]
