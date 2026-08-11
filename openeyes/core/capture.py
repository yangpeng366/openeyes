"""Screen / window capture."""
from __future__ import annotations
from PIL import Image


def capture_screen() -> Image.Image:
    """Capture the full primary screen (or all monitors if multi-screen)."""
    from openeyes.backends import uia as backend
    return backend.capture_screen()


def capture_window(hwnd: int) -> Image.Image:
    """Capture a specific top-level window by hwnd."""
    from openeyes.backends import uia as backend
    return backend.capture_window(hwnd)