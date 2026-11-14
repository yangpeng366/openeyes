"""OCR backend for scene text reading.

The scene-text pipeline needs one stable shape out of "whatever OCR engine
this machine happens to have". This module owns that shape and nothing else:

* :func:`ocr_engine` resolves an engine - an explicit one wins, otherwise
  RapidOCR is loaded lazily and cached at module level.
* :func:`read_text` normalises any supported engine payload into a list of
  ``{'text', 'box', 'bbox', 'score'}`` dicts.

The engine is an optional dependency: when ``rapidocr_onnxruntime`` is
missing the caller gets :class:`OCRUnavailable` and degrades to another
source instead of crashing. Nothing heavy is imported at module import time.

Colour convention: OpenEyes speaks RGB everywhere (PIL images, numpy arrays).
RapidOCR speaks BGR on its numpy path and converts RGB to BGR itself on its
PIL path, so :class:`RapidOCREngine` channel-reverses numpy input. Without
that step a red control would reach the detector with red and blue swapped.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np

OCR_INSTALL_HINT = "pip install rapidocr_onnxruntime"

#: Module level cache for the lazily loaded engine.
_ENGINE: Any = None


class OCRUnavailable(RuntimeError):
    """Raised when no OCR engine can be loaded."""


class RapidOCREngine:
    """Callable adapter that keeps RapidOCR's colour convention honest.

    RapidOCR treats numpy input as BGR (the OpenCV convention) and converts
    PIL input from RGB to BGR itself. Both paths therefore end up with the
    same pixels, which is what OpenEyes expects to read.
    """

    def __init__(self, engine: Any) -> None:
        self._engine = engine

    def __call__(self, image: Any) -> Any:
        return self._engine(self._prepare(image))

    def __getattr__(self, name: str) -> Any:
        """Delegate unknown attributes to the wrapped engine."""
        try:
            engine = self.__dict__["_engine"]
        except KeyError as exc:  # pragma: no cover - defensive
            raise AttributeError(name) from exc
        return getattr(engine, name)

    @staticmethod
    def _prepare(image: Any) -> Any:
        """Return the image in the channel order RapidOCR expects."""
        if not isinstance(image, np.ndarray) or image.ndim != 3 or image.shape[2] < 3:
            return image
        array = image
        if array.dtype != np.uint8:
            array = np.clip(array, 0, 255).astype(np.uint8)
        return np.ascontiguousarray(array[:, :, :3][:, :, ::-1])


def ocr_engine(engine: Any = None) -> Any:
    """Return a callable OCR engine.

    An explicit engine is returned unchanged, so callers and tests keep full
    control over what runs. Without one, RapidOCR is loaded lazily and cached
    at module level. :class:`OCRUnavailable` is raised when the optional
    dependency is missing or fails to initialise.
    """
    global _ENGINE
    if engine is not None:
        return engine
    if _ENGINE is None:
        _ENGINE = _load_engine()
    return _ENGINE


def _load_engine() -> Any:
    """Load RapidOCR, translating every failure into OCRUnavailable."""
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError as exc:
        raise OCRUnavailable(
            f"rapidocr_onnxruntime is not available ({exc}); "
            f"install it with `{OCR_INSTALL_HINT}`"
        ) from exc
    try:
        rapid = RapidOCR()
    except Exception as exc:
        raise OCRUnavailable(
            f"RapidOCR could not be initialised ({exc}); check the onnxruntime "
            "runtime and the bundled model files"
        ) from exc
    return RapidOCREngine(rapid)


def read_text(image: Any, *, engine: Any = None) -> list[dict[str, Any]]:
    """Run OCR over one image and return structured text items.

    ``image`` is a PIL image or a numpy array (RGB, HxWx3). The result is one
    dict per text line::

        {'text': str, 'box': [[x, y], ...], 'bbox': (x, y, w, h), 'score': float}

    ``box`` is the polygon as reported by the engine and ``bbox`` its axis
    aligned bounding box in pixels. Engine exceptions are not swallowed - the
    caller owns the degradation path.

    An injected engine is called with the image unchanged; the default engine
    receives it in the colour order it expects.
    """
    active = ocr_engine(engine)
    return _normalize(active(image))


def _normalize(payload: Any) -> list[dict[str, Any]]:
    """Turn an engine payload into the stable item list."""
    items: list[dict[str, Any]] = []
    for raw in _iter_items(payload):
        item = _normalize_item(raw)
        if item is not None:
            items.append(item)
    items.sort(key=lambda item: (item["bbox"][1], item["bbox"][0]))
    return items


def _iter_items(payload: Any) -> list[Any]:
    """Return the raw item list for either supported engine shape.

    Engines return either ``[[poly, text, score], ...]`` or a
    ``(results, elapse)`` pair; RapidOCR uses the pair and reports ``None``
    when nothing was detected.
    """
    if payload is None:
        return []
    if isinstance(payload, tuple):
        if not payload:
            return []
        return _iter_items(payload[0])
    if isinstance(payload, list):
        if len(payload) == 2 and _is_numberish(payload[1]):
            return _iter_items(payload[0])
        return list(payload)
    return []


def _is_numberish(value: Any) -> bool:
    """True for a numeric value or a sequence of numbers (the elapse payload)."""
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return True
    if isinstance(value, (list, tuple)):
        return all(
            isinstance(item, (int, float)) and not isinstance(item, bool) for item in value
        )
    return False


def _is_scalar_number(value: Any) -> bool:
    """True for a plain number (not a list, not a bool)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _normalize_item(raw: Any) -> dict[str, Any] | None:
    """Normalise one engine item, or None when it carries no usable text."""
    if not isinstance(raw, (list, tuple)) or len(raw) < 3:
        return None
    points = _normalize_points(raw[0])
    if points is None:
        return None
    text = str(raw[1]).strip() if raw[1] is not None else ""
    if not text:
        return None
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    left, right = min(xs), max(xs)
    top, bottom = min(ys), max(ys)
    return {
        "text": text,
        "box": [[x, y] for x, y in points],
        "bbox": (left, top, right - left, bottom - top),
        "score": _clamp_score(raw[2]),
    }


def _normalize_points(poly: Any) -> list[tuple[int, int]] | None:
    """Return the polygon as integer pixel points, or None when unusable."""
    if not isinstance(poly, (list, tuple)):
        return None
    values = list(poly)
    if values and all(_is_scalar_number(value) for value in values):
        if len(values) < 4 or len(values) % 2:
            return None
        values = [values[index:index + 2] for index in range(0, len(values), 2)]
    points: list[tuple[int, int]] = []
    for point in values:
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            return None
        try:
            x = float(point[0])
            y = float(point[1])
        except (TypeError, ValueError):
            return None
        if not (math.isfinite(x) and math.isfinite(y)):
            return None
        points.append((int(round(x)), int(round(y))))
    return points or None


def _clamp_score(value: Any) -> float:
    """Clamp a confidence value into 0..1; unusable values become 0.0."""
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(score):
        return 0.0
    return min(1.0, max(0.0, score))
