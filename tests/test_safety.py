import pytest

from openeyes.core.schema import BBox, Center, Element, ElementState
from openeyes.core.safety import is_editable_element, validate_text_entry


def _element(control_type="Edit", *, focused=False, enabled=True):
    return Element(
        backend="cdp",
        control_type=control_type,
        name="Search",
        bbox=BBox(0, 0, 10, 10),
        center=Center(5, 5),
        state=ElementState(enabled=enabled, visible=True, focused=focused),
    )


def test_editable_element_accepts_edit_and_rejects_button():
    assert is_editable_element(_element())
    assert not is_editable_element(_element("Button"))


def test_validate_text_entry_requires_editable_target():
    validate_text_entry("hello", _element())
    with pytest.raises(ValueError, match="not editable"):
        validate_text_entry("hello", _element("Button"))


def test_validate_text_entry_rejects_unauthorized_expansion():
    with pytest.raises(ValueError, match="not authorized"):
        validate_text_entry("hello; drop table", _element(), source_text="say hello")


def test_validate_text_entry_allows_equivalent_whitespace_and_case():
    validate_text_entry(" Say  HELLO ", _element(), source_text="please say hello")