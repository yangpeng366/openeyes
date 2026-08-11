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

from openeyes.core.schema import BBox, Center, Element, ElementState, WindowInfo
from openeyes.core.windows import list_windows, find_window
from openeyes.core.capture import capture_window, capture_screen
from openeyes.core.selector import detect_elements, find_elements, click_by_selector
from openeyes.core.actuator import click_xy, send_hotkey, type_text, drag, scroll

__version__ = "0.1.0"

__all__ = [
    "BBox", "Center", "Element", "ElementState", "WindowInfo",
    "list_windows", "find_window",
    "capture_window", "capture_screen",
    "detect_elements", "find_elements", "click_by_selector",
    "click_xy", "send_hotkey", "type_text", "drag", "scroll",
    "__version__",
]