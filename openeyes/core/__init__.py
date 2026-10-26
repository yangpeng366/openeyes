"""Core primitives — capture, detect, actuate."""
from openeyes.core.schema import BBox, Center, Element, ElementState, WindowInfo
from openeyes.core.dpi import ensure_dpi_awareness, current_dpi_awareness
from openeyes.core.windows import list_windows, find_window
from openeyes.core.capture import capture_window, capture_screen
from openeyes.core.selector import (detect_elements, find_elements,
                                   click_by_selector, click_element)
from openeyes.core.actuator import (click_xy, send_hotkey, type_text, drag, scroll,
                                    move_to, cursor_pos)

__all__ = [
    "BBox", "Center", "Element", "ElementState", "WindowInfo",
    "list_windows", "find_window",
    "capture_window", "capture_screen",
    "detect_elements", "find_elements", "click_by_selector", "click_element",
    "click_xy", "send_hotkey", "type_text", "drag", "scroll",
    "move_to", "cursor_pos",
    "ensure_dpi_awareness", "current_dpi_awareness",
]