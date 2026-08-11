"""Detect elements via backend, then filter by selector."""
from __future__ import annotations
import re
from openeyes.core.schema import Element


def detect_elements(hwnd: int, *, restore: bool = False,
                    max_depth: int = 12) -> list[Element]:
    """Enumerate interactive elements in a window via the platform backend.

    On UWP / Windows Store apps, the accessibility tree is empty when the
    window is minimized. Pass ``restore=True`` to call ShowWindow(SW_RESTORE)
    first.
    """
    from openeyes.backends import uia as backend
    return backend.detect_elements(hwnd, restore=restore, max_depth=max_depth)


def find_elements(elements: list[Element], *,
                  name_contains: str | None = None,
                  control_type: str | None = None,
                  regex: str | None = None) -> list[Element]:
    """Filter an element list by name (substring) / control_type (exact) / regex."""
    out: list[Element] = []
    for e in elements:
        if control_type and e.control_type != control_type:
            continue
        if name_contains and name_contains.lower() not in (e.name or "").lower():
            continue
        if regex and not re.search(regex, e.name or "", re.IGNORECASE):
            continue
        out.append(e)
    return out


def click_by_selector(hwnd: int, *, name_contains: str | None = None,
                      control_type: str | None = None,
                      regex: str | None = None,
                      button: str = "left", double: bool = False,
                      dry_run: bool = True) -> Element:
    """One-shot: detect, filter, click first match. Dry-run by default."""
    elements = detect_elements(hwnd, restore=True)
    matches = find_elements(elements, name_contains=name_contains,
                            control_type=control_type, regex=regex)
    if not matches:
        raise LookupError(
            f"no element matches name_contains={name_contains!r} "
            f"control_type={control_type!r} regex={regex!r} in hwnd={hwnd}"
        )
    target = matches[0]
    if not dry_run:
        from openeyes.core.actuator import click_xy
        click_xy(target.center.x, target.center.y, button=button, double=double)
    return target