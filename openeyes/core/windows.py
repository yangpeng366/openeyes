"""List / find top-level windows."""
from __future__ import annotations
import re
import sys
from openeyes.core.schema import WindowInfo


def list_windows() -> list[WindowInfo]:
    """Enumerate all visible top-level windows."""
    backend = _platform_backend()
    return backend.list_windows()


def find_window(*, title_contains: str | None = None,
                class_name: str | None = None,
                regex: str | None = None) -> list[WindowInfo]:
    """Filter windows by title / class / regex."""
    wins = list_windows()
    out: list[WindowInfo] = []
    for w in wins:
        if title_contains and title_contains.lower() not in w.title.lower():
            continue
        if class_name and class_name.lower() not in w.class_name.lower():
            continue
        if regex and not re.search(regex, w.title, re.IGNORECASE):
            continue
        out.append(w)
    return out


def _platform_backend():
    """Lazy import to avoid pulling platform deps on every import."""
    if sys.platform.startswith("win"):
        from openeyes.backends import uia as uia_backend
        return uia_backend
    if sys.platform == "darwin":
        try:
            from openeyes.backends import ax as ax_backend  # type: ignore
            return ax_backend
        except ImportError:
            raise NotImplementedError("macOS AX backend not yet implemented")
    if sys.platform.startswith("linux"):
        try:
            from openeyes.backends import atspi as atspi_backend  # type: ignore
            return atspi_backend
        except ImportError:
            raise NotImplementedError("Linux AT-SPI backend not yet implemented")
    raise NotImplementedError(f"unsupported platform: {sys.platform}")