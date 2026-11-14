"""Render a scene dict as plain text that a text-only agent can read.

A scene is the dict produced by ``openeyes.core.scene.describe_window``:
one header line describing the window, then one line per element. The
renderer is deterministic and defensive - a missing or malformed scene
still yields a readable header instead of an exception.

The output uses ASCII punctuation and Chinese category names only; no
ANSI colour, no emoji.
"""
from __future__ import annotations

from typing import Any

#: Chinese display names for the element kinds a scene can carry.
KIND_NAMES: dict[str, str] = {
    "button": "按钮",
    "label": "文本",
    "input": "输入框",
    "checkbox": "复选框",
    "icon": "图标",
    "panel": "面板",
    "menu": "菜单",
    "unknown": "未知",
}


def _number(value: Any) -> float | None:
    """Return ``value`` as a float, or None when it is not a number."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _integer(value: float) -> int:
    """Round a coordinate or dimension to the nearest integer."""
    return int(round(value))


def _dimension(value: Any) -> str | None:
    """Format a window dimension as an integer string."""
    number = _number(value)
    return None if number is None else str(_integer(number))


def _window(scene: dict[str, Any]) -> dict[str, Any]:
    """The scene's window dict, or an empty one when it is missing."""
    window = scene.get("window")
    return window if isinstance(window, dict) else {}


def _title(window: dict[str, Any]) -> str:
    """The window title, falling back to the class name, else empty."""
    for key in ("title", "class_name"):
        value = window.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _size(window: dict[str, Any]) -> str:
    """The window size as ``<w>x<h>``, or empty when unavailable."""
    size = window.get("size")
    if isinstance(size, (list, tuple)) and len(size) >= 2:
        width = _dimension(size[0])
        height = _dimension(size[1])
        if width is not None and height is not None:
            return f"{width}x{height}"
    return ""


def _header(scene: dict[str, Any], count: int) -> str:
    """Build the first line describing the window and element count."""
    window = _window(scene)
    parts = ["窗口"]
    title = _title(window)
    if title:
        parts.append(title)
    size = _size(window)
    if size:
        parts.append(size)
    source = scene.get("source")
    if isinstance(source, str) and source.strip():
        parts.append(f"来源={source.strip()}")
    parts.append(f"元素={count}")
    return " ".join(parts)


def _elements(scene: dict[str, Any]) -> list[dict[str, Any]]:
    """The scene's element dicts; anything else is ignored."""
    raw = scene.get("elements")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def _bbox(element: dict[str, Any]) -> dict[str, Any]:
    """The element's bbox dict, or an empty one when it is missing."""
    bbox = element.get("bbox")
    return bbox if isinstance(bbox, dict) else {}


def _center(element: dict[str, Any]) -> tuple[int, int]:
    """The element's click point: ``center``, else the bbox midpoint."""
    center = element.get("center")
    if isinstance(center, dict):
        x = _number(center.get("x"))
        y = _number(center.get("y"))
        if x is not None and y is not None:
            return _integer(x), _integer(y)
    bbox = _bbox(element)
    x = _number(bbox.get("x"))
    y = _number(bbox.get("y"))
    if x is None or y is None:
        return 0, 0
    width = _number(bbox.get("w")) or 0.0
    height = _number(bbox.get("h")) or 0.0
    return _integer(x + width / 2), _integer(y + height / 2)


def _sort_key(element: dict[str, Any]) -> tuple[float, float]:
    """Reading order: bbox top edge first, then bbox left edge."""
    bbox = _bbox(element)
    top = _number(bbox.get("y"))
    left = _number(bbox.get("x"))
    if top is None or left is None:
        x, y = _center(element)
        if top is None:
            top = float(y)
        if left is None:
            left = float(x)
    return top, left


def _kind(element: dict[str, Any]) -> str:
    """The element's Chinese category name, or its raw kind."""
    kind = element.get("kind")
    if isinstance(kind, str) and kind.strip():
        name = kind.strip()
        return KIND_NAMES.get(name, name)
    return KIND_NAMES["unknown"]


def _text(element: dict[str, Any]) -> str:
    """The element's text with whitespace collapsed to single spaces."""
    text = element.get("text")
    if not isinstance(text, str):
        return ""
    return " ".join(text.split())


def _score(element: dict[str, Any]) -> float:
    """The element's score, defaulting to 0.0 when missing or invalid."""
    score = _number(element.get("score"))
    return 0.0 if score is None else score


def _element_line(element: dict[str, Any]) -> str:
    """Render one element as ``[<kind>] <text> @(<cx>,<cy>) <score>``."""
    x, y = _center(element)
    parts = [f"[{_kind(element)}]"]
    text = _text(element)
    if text:
        parts.append(text)
    parts.append(f"@({x},{y})")
    parts.append(f"{_score(element):.2f}")
    return " ".join(parts)


def _ellipsis(omitted_lines: int, omitted_chars: int) -> str:
    """The final line reporting what was dropped."""
    return f"... 省略 {omitted_lines} 行 {omitted_chars} 字符"


def render_scene(scene: dict[str, Any], *, max_lines: int = 200,
                 max_chars: int = 6000) -> str:
    """Render ``scene`` as a compact, text-only digest.

    The first line describes the window; each following line describes one
    element in reading order ``(y, x)``. ``max_lines`` bounds the header
    plus element lines and ``max_chars`` bounds their joined length; when
    either is exceeded the remaining elements are dropped and one extra
    line reports how many lines and characters were omitted. A malformed
    or empty scene still produces a readable header.
    """
    if not isinstance(scene, dict):
        scene = {}
    elements = _elements(scene)
    lines = [_header(scene, len(elements))]
    limit_lines = max(1, max_lines)
    limit_chars = max(1, max_chars)
    ordered = sorted(elements, key=_sort_key)
    kept = 0
    for element in ordered:
        if len(lines) >= limit_lines:
            break
        line = _element_line(element)
        if len("\n".join([*lines, line])) > limit_chars:
            break
        lines.append(line)
        kept += 1
    omitted = ordered[kept:]
    if omitted:
        omitted_chars = len("\n".join(_element_line(e) for e in omitted))
        lines.append(_ellipsis(len(omitted), omitted_chars))
    return "\n".join(lines)
