"""Detect elements via backend, then filter by selector."""
from __future__ import annotations

import re
from typing import Any

from openeyes.core.schema import Element


def detect_elements(hwnd: int, *, restore: bool = False,
                    max_depth: int | None = None) -> list[Element]:
    """Enumerate interactive elements in a window via the platform backend.

    On UWP / Windows Store apps, the accessibility tree is empty when the
    window is minimized. Pass ``restore=True`` to call ShowWindow(SW_RESTORE)
    first.
    """
    from openeyes.backends import uia as backend
    if max_depth is None:
        max_depth = backend.DEFAULT_MAX_DEPTH
    return backend.detect_elements(hwnd, restore=restore, max_depth=max_depth)


def find_elements(elements: list[Element], *,
                  name_contains: str | None = None,
                  control_type: str | None = None,
                  regex: str | None = None,
                  automation_id: str | None = None) -> list[Element]:
    """Filter an element list by name (substring) / control_type (exact) / regex.

    ``automation_id`` matches the stable control identifier from the
    accessibility tree — prefer it over the visible name when the label
    changes at runtime (countdowns, spinners, "0:18 left - click to reset").
    """
    out: list[Element] = []
    for e in elements:
        if control_type and e.control_type != control_type:
            continue
        if automation_id and e.automation_id != automation_id:
            continue
        if name_contains and name_contains.lower() not in (e.name or "").lower():
            continue
        if regex and not re.search(regex, e.name or "", re.IGNORECASE):
            continue
        out.append(e)
    return out


def _window_origin(hwnd: int) -> tuple[int, int]:
    """The window's top-left corner in absolute screen pixels.

    The UIA backend measures *inside* the window; ``click_xy`` speaks
    absolute screen pixels. Reading the rectangle is the only way back
    across that gap, and it is read here - at the click - rather than
    cached from a capture that may be stale by now.
    """
    import win32gui
    left, top, _right, _bottom = win32gui.GetWindowRect(hwnd)
    return int(left), int(top)


def click_by_selector(hwnd: int, *, name_contains: str | None = None,
                      control_type: str | None = None,
                      regex: str | None = None,
                      automation_id: str | None = None,
                      button: str = "left", double: bool = False,
                      dry_run: bool = True) -> Element:
    """One-shot: detect, filter, click first match. Dry-run by default.

    The matched element's ``center`` is window-relative (UIA), but
    ``click_xy`` takes absolute screen pixels: the window origin is added
    back through :func:`openeyes.core.coords.to_screen_point` before the
    pointer moves. A conversion that cannot be done - no window rectangle,
    or a backend with no known coordinate space - raises instead of
    clicking one window origin away from the control.
    """
    elements = detect_elements(hwnd, restore=True)
    matches = find_elements(elements, name_contains=name_contains,
                            control_type=control_type, regex=regex,
                            automation_id=automation_id)
    if not matches:
        raise LookupError(
            f"no element matches name_contains={name_contains!r} "
            f"control_type={control_type!r} regex={regex!r} "
            f"automation_id={automation_id!r} in hwnd={hwnd}"
        )
    target = matches[0]
    if not dry_run:
        from openeyes.core.actuator import click_xy
        from openeyes.core.coords import to_screen_point

        origin = _window_origin(hwnd)
        x, y = to_screen_point(target, window_origin=origin)
        click_xy(x, y, button=button, double=double)
    return target


def click_element(hwnd: int, *, name_contains: str | None = None,
                  name_regex: str | None = None,
                  control_type: str | None = None,
                  automation_id: str | None = None,
                  method: str = "auto", dry_run: bool = True,
                  budget: Any | None = None,
                  lease: Any | None = None) -> dict:
    """Click an element through the platform backend.

    Unlike :func:`click_by_selector` this asks the accessibility tree to
    perform the click (Invoke pattern) whenever the control supports it,
    which needs no pixel maths, no window focus and never moves the user's
    mouse. See ``openeyes.backends.uia.click_element`` for the report
    fields and for the delivery ladder.

    ``budget`` is an ``openeyes.agent.profile.FocusBudget``. When one is
    given, the rungs that reach for the user's pointer are paid for from
    it; without one, nothing is gated.
    """
    from openeyes.backends import uia as backend
    return backend.click_element(
        hwnd, automation_id=automation_id, name_regex=name_regex,
        name_contains=name_contains, control_type=control_type,
        method=method, dry_run=dry_run, budget=budget, lease=lease,
    )