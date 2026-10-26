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


def click_by_selector(hwnd: int, *, name_contains: str | None = None,
                      control_type: str | None = None,
                      regex: str | None = None,
                      automation_id: str | None = None,
                      button: str = "left", double: bool = False,
                      dry_run: bool = True) -> Element:
    """One-shot: detect, filter, click first match. Dry-run by default."""
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
        click_xy(target.center.x, target.center.y, button=button, double=double)
    return target


def click_element(hwnd: int, *, name_contains: str | None = None,
                  name_regex: str | None = None,
                  control_type: str | None = None,
                  automation_id: str | None = None,
                  method: str = "auto", dry_run: bool = True) -> dict:
    """Click an element through the platform backend.

    Unlike :func:`click_by_selector` this asks the accessibility tree to
    perform the click (Invoke pattern) whenever the control supports it,
    which needs no pixel maths, no window focus and never moves the user's
    mouse. See ``openeyes.backends.uia.click_element`` for the report
    fields.
    """
    from openeyes.backends import uia as backend
    return backend.click_element(
        hwnd, automation_id=automation_id, name_regex=name_regex,
        name_contains=name_contains, control_type=control_type,
        method=method, dry_run=dry_run,
    )