'''Safety guards inspired by third-hand TextEntryPlan.'''
from __future__ import annotations

from openeyes.core.schema import Element


class TextEntryValidationError(ValueError):
    """Raised before text is sent to an unverified target."""


def is_foreground_window(hwnd: int) -> bool:
    """Return whether the exact top-level window owns desktop focus."""
    if not hwnd:
        return False
    import win32gui
    try:
        return win32gui.GetForegroundWindow() == hwnd
    except Exception:
        return False


def _visible_enabled(element: Element) -> bool:
    return bool(element.state.enabled and element.state.visible)


def is_editable_element(element: Element | None) -> bool:
    """Whether an Element is a likely text-entry control.

    CDP maps input/textarea to Edit; contenteditable controls currently keep
    their tag-derived type, so known editable tag names are accepted too.
    """
    if element is None or not element.interactive or not _visible_enabled(element):
        return False
    control_type = (element.control_type or "").casefold()
    class_name = (element.class_name or "").casefold()
    editable_types = {"edit", "textarea", "textinput", "searchbox"}
    return control_type in editable_types or "contenteditable" in class_name


def validate_text_entry(
    text: str,
    element: Element | None,
    *,
    source_text: str | None = None,
) -> None:
    """Validate literal text and target before actuation.

    ``source_text`` is the user-provided request/text that authorizes this
    operation. When supplied, the typed text must occur in it, preventing a
    planner from expanding the request into new instructions.
    """
    if not isinstance(text, str) or not text:
        raise TextEntryValidationError("text must be a non-empty string")
    if source_text is not None:
        normalized_text = " ".join(text.split()).casefold()
        normalized_source = " ".join(source_text.split()).casefold()
        if not normalized_text or normalized_text not in normalized_source:
            raise TextEntryValidationError(
                "text is not authorized by source_text"
            )
    if element is None:
        raise TextEntryValidationError("no editable focused target")
    if not is_editable_element(element):
        raise TextEntryValidationError(
            f"target is not editable: {element.control_type!r}"
        )