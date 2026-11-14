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


def capture_window_printwindow(hwnd: int) -> Image.Image:
    """Capture a window's own pixels, even when another window covers it.

    Falls back to the screen grab when the window refuses to paint, so the
    caller always gets an image; ``capture_window_best`` reports which path
    was taken.
    """
    from openeyes.backends import uia as backend
    image = backend.capture_window_printwindow(hwnd)
    if image is not None:
        return image
    return backend.capture_window(hwnd)


def _is_blank(image: Image.Image) -> bool:
    """True when a capture carries no picture (a flat, single-colour frame).

    Some toolkits answer ``PrintWindow`` with an empty surface; the frame is
    then useless even though the call succeeded.
    """
    try:
        import numpy as np

        array = np.asarray(image.convert("L"), dtype="float32")
        return float(array.std()) < 3.0
    except Exception:
        return False


def capture_window_best(hwnd: int) -> tuple[Image.Image, str]:
    """Capture a window and name the method used.

    Returns ``(image, "printwindow" | "screen")``.  The screen path copies
    whatever is on top, so a caller may want to know the frame is occluded.
    """
    from openeyes.backends import uia as backend
    image = backend.capture_window_printwindow(hwnd)
    if image is not None and not _is_blank(image):
        return image, "printwindow"
    return backend.capture_window(hwnd), "screen"
