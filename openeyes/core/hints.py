"""Vimium-style letter hint assignment for interactive elements.

After ``detect_elements`` returns an ``Element[]``, callers can invoke
``assign_hints`` to give every element a short unique string the user (or an
LLM agent picking a candidate by alphabetical anchor) can type to address it.

Algorithm (matches the Vimium / Surfingkeys convention):

* Charset = home-row keys ``asdfqwerzxcv`` (12 letters; all lowercase).
* Elements are sorted by *reading order* - top-to-bottom (bbox.y), breaking
  ties by left-to-right (bbox.x).
* We assign the **shortest unique hint** to each element: a 1-letter hint if
  it is unique among every other 1-letter assignment, else 2 letters, else 3.
* No element ever shares a hint with another.

This mirrors the experience of Vimium but is also useful for LLM-driven flows:
the model can answer "click element ``s``" without describing coordinates.
"""
from __future__ import annotations
from typing import Iterable

from openeyes.core.schema import Element

# Home-row charset used by Vimium-like hints (lowercase for less visual noise).
CHARSET = "asdfqwerzxcv"


def _sort_key(e: Element) -> tuple[int, int, int, int]:
    """Reading order: top->down, then left->right, then size (largest first).

    Using ``bbox.y`` (top edge) and ``bbox.x`` (left edge) keeps elements on
    the same line adjacent; the ``h`` then ``w`` tie-breakers keep the
    visually-larger button earlier inside a grid cell (Vimium's behaviour).
    """
    return (e.bbox.y, e.bbox.x, -e.bbox.h, -e.bbox.w)


def _hints_for_n(n: int) -> list[str]:
    """All length-1, length-2, then length-3 hints (lazy generator).

    Caps at the natural maximum of ``12 + 144 + 12^3 = 1884`` unique hints.
    Callers with more items than this should split the page (only first 1884
    receive a hint); we never loop the pool indefinitely because that would
    produce visually unhelpful 4-letter strings.
    """
    out: list[str] = []
    for c in CHARSET:
        out.append(c)
        if len(out) >= n:
            return out
    for a in CHARSET:
        for b in CHARSET:
            out.append(a + b)
            if len(out) >= n:
                return out
    for a in CHARSET:
        for b in CHARSET:
            for c in CHARSET:
                out.append(a + b + c)
                if len(out) >= n:
                    return out
    return out


def assign_hints(elements: Iterable[Element]) -> list[Element]:
    """Assign a unique Vimium-style letter hint to each element in-place.

    Returns the same list (sorted by reading order). Empty input returns [].
    Visible-by-bbox elements without a positive area are skipped - they tend
    to be DOM artefacts (zero-width labels) and break hint assignment.
    """
    items = [e for e in elements if e.bbox.w > 0 and e.bbox.h > 0]
    if not items:
        return []
    items.sort(key=_sort_key)
    pool = _hints_for_n(len(items))
    for element, hint in zip(items, pool):
        element.hint = hint
    # Elements filtered out earlier: clear any stale hint so output is honest.
    for e in elements:
        if e.bbox.w <= 0 or e.bbox.h <= 0:
            e.hint = None
    return items


def find_by_hint(elements: list[Element], hint: str) -> Element | None:
    """Look up an element by its assigned hint letter (case-insensitive)."""
    if not hint:
        return None
    target = hint.lower()
    for e in elements:
        if e.hint and e.hint.lower() == target:
            return e
    return None
