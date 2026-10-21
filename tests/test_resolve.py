"""Tests for the structured-first backend degradation chain.

The chain encodes one rule: prefer structured backends, fall back to pixels
last, and leave a trail explaining every step.
"""
from __future__ import annotations

from openeyes.core.resolve import resolve_elements
from openeyes.core.schema import BBox, Center, Element


def _el(name: str = "ok", backend: str = "uia") -> Element:
    return Element(
        backend=backend,
        control_type="Button",
        name=name,
        bbox=BBox(0, 0, 10, 10),
        center=Center(5, 5),
    )


def test_uia_answers_first(monkeypatch):
    monkeypatch.setattr("openeyes.core.resolve._try_uia",
                        lambda hwnd, depth: [_el()])
    result = resolve_elements(hwnd=123)
    assert result.used_backend == "uia"
    assert len(result.elements) == 1


def test_empty_uia_falls_through_to_cdp(monkeypatch):
    monkeypatch.setattr("openeyes.core.resolve._try_uia", lambda hwnd, depth: [])
    monkeypatch.setattr("openeyes.core.resolve._try_cdp",
                        lambda port, url: [_el(backend="cdp")])
    result = resolve_elements(hwnd=123, port=9222)
    assert result.used_backend == "cdp"
    assert [a.backend for a in result.attempts] == ["uia", "cdp"]
    assert result.attempts[0].count == 0
    assert result.attempts[0].reason == "no elements"


def test_failing_backend_is_recorded_and_the_chain_continues(monkeypatch):
    def boom(hwnd, depth):
        raise RuntimeError("accessibility tree unavailable")

    monkeypatch.setattr("openeyes.core.resolve._try_uia", boom)
    monkeypatch.setattr("openeyes.core.resolve._try_cdp",
                        lambda port, url: [_el(backend="cdp")])
    result = resolve_elements(hwnd=123, port=9222)
    assert result.used_backend == "cdp"
    assert result.attempts[0].ok is False
    assert "accessibility tree unavailable" in result.attempts[0].reason


def test_missing_inputs_are_skipped_with_a_reason():
    result = resolve_elements(hwnd=None, port=None, vision_elements=None)
    assert result.used_backend is None
    assert [a.backend for a in result.attempts] == ["uia", "cdp", "vision"]
    assert all(a.ok is False for a in result.attempts)


def test_vision_tier_uses_precomputed_elements():
    result = resolve_elements(hwnd=None, port=None,
                              vision_elements=[_el(backend="vision")])
    assert result.used_backend == "vision"
    assert result.elements[0].backend == "vision"


def test_vision_tier_uses_a_parser_callable():
    result = resolve_elements(
        hwnd=None, port=None, image_path="shot.png",
        vision_parser=lambda path: [_el(backend="vision")],
    )
    assert result.used_backend == "vision"


def test_nothing_found_leaves_the_backend_unset():
    result = resolve_elements(hwnd=None, port=None, vision_elements=[])
    assert result.used_backend is None
    assert result.elements == []
    assert result.attempts[-1].backend == "vision"


def test_order_is_respected(monkeypatch):
    monkeypatch.setattr("openeyes.core.resolve._try_cdp",
                        lambda port, url: [_el(backend="cdp")])
    result = resolve_elements(hwnd=None, port=9222, order=("cdp", "uia"))
    assert result.used_backend == "cdp"
    assert [a.backend for a in result.attempts] == ["cdp"]


def test_result_to_dict_shape():
    result = resolve_elements(hwnd=None, port=None,
                              vision_elements=[_el(backend="vision")])
    payload = result.to_dict()
    assert payload["used_backend"] == "vision"
    assert payload["elements"][0]["backend"] == "vision"
    assert payload["attempts"][-1]["backend"] == "vision"