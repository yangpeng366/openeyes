"""Tests for the Vimium-style letter hint assignment."""
from __future__ import annotations

from openeyes.core.hints import (
    CHARSET, assign_hints, find_by_hint, _hints_for_n, _sort_key,
)
from openeyes.core.schema import BBox, Center, Element


def _mk(name: str, x: int, y: int, w: int = 80, h: int = 24,
        backend: str = "cdp", control_type: str = "Button") -> Element:
    return Element(
        backend=backend, control_type=control_type, name=name,
        bbox=BBox(x, y, w, h), center=Center(x + w // 2, y + h // 2),
    )


def test_assign_hints_sorts_by_reading_order():
    # top(10,10) -> bottom(10,500) -> middle(100,100)
    elems = [
        _mk("middle", 100, 100),
        _mk("top",    10,  10),
        _mk("bottom", 10,  500),
    ]
    out = assign_hints(elems)
    # Filter zero-area is moot here; sort by (y, x) -> top, bottom, middle.
    assert [e.name for e in out if e.name] == ["top", "middle", "bottom"]


def test_assign_hints_uses_charset_in_order():
    elems = [_mk(f"e{i}", 10 + i * 50, 10) for i in range(len(CHARSET))]
    assign_hints(elems)
    assert [e.hint for e in elems] == list(CHARSET)


def test_assign_hints_two_letter_after_single_letter_pool():
    elems = [_mk(f"e{i}", 10 + (i % 12) * 30, 10 + (i // 12) * 30)
             for i in range(20)]
    assign_hints(elems)
    assert elems[0].hint == "a"
    assert elems[len(CHARSET) - 1].hint == "v"
    assert elems[len(CHARSET)].hint == "aa"


def test_assign_hints_three_letter_kicks_in_after_156():
    elems = [_mk(f"e{i}", 10 + (i % 12) * 30, 10 + (i // 12) * 30)
             for i in range(160)]
    assign_hints(elems)
    # 12 + 144 = 156 two-or-fewer-letter pool slots.
    assert elems[156].hint == "aaa"
    uniques = {e.hint for e in elems}
    assert len(uniques) == len(elems) == 160


def test_assign_hints_caps_at_1884_hints():
    elems = [_mk(f"e{i}", 10 + i, 10) for i in range(2000)]
    assign_hints(elems)
    assigned = [e for e in elems if e.hint]
    # 1884 = 12 + 144 + 12^3 — Vimium-style hard cap.
    assert len(assigned) == 12 + 144 + 12 * 12 * 12


def test_assign_hints_skips_zero_area():
    elems = [
        _mk("real", 10, 10, w=80, h=24),
        _mk("ghost", 0, 0, w=0, h=0),
    ]
    assign_hints(elems)
    assert elems[0].hint == "a"
    assert elems[1].hint is None


def test_find_by_hint_case_insensitive_and_missing():
    elems = [_mk(f"e{i}", i * 30, 10) for i in range(3)]
    assign_hints(elems)
    a = find_by_hint(elems, "a")
    assert a and a.name == "e0"
    assert find_by_hint(elems, "A").name == "e0"
    assert find_by_hint(elems, "zzz") is None
    assert find_by_hint(elems, "") is None


def test_hints_for_n_cap_at_3_chars():
    pool_5 = _hints_for_n(5)
    assert pool_5 == list(CHARSET[:5])
    pool_2000 = _hints_for_n(2000)
    # Full pool = 12 + 144 + 12^3 = 1884 unique 1/2/3-letter hints.
    assert len(pool_2000) == 1884
    assert all(len(h) <= 3 for h in pool_2000)
    assert len(set(pool_2000)) == len(pool_2000)


def test_sort_key_prefers_top_then_left_then_size():
    a = _mk("a", 10, 10, w=80, h=24)
    b = _mk("b", 10, 11, w=80, h=24)   # one row lower
    c = _mk("c", 0, 10, w=80, h=24)    # same row, further left
    items = sorted([b, c, a], key=_sort_key)
    # y asc: c(10) == a(10), b(11)
    # tie on y -> x asc: c(0), a(10)
    assert [e.name for e in items] == ["c", "a", "b"]


def test_empty_input_returns_empty():
    assert assign_hints([]) == []
    assert find_by_hint([], "a") is None


def test_assign_hints_preserves_relative_order_within_row():
    # Same y; should sort by x.
    elems = [_mk("right", 200, 50), _mk("left", 50, 50)]
    out = assign_hints(elems)
    assert out[0].name == "left"
    assert out[1].name == "right"
