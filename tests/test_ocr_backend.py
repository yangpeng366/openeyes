"""Synthetic tests for the OCR backend.

No test needs the real OCR engine: every case runs against a fake engine, so
the module stays testable on machines without ``rapidocr_onnxruntime``.
"""
from __future__ import annotations

import sys
import types

import numpy as np
import pytest
from PIL import Image

from openeyes.backends import ocr as ocr_backend
from openeyes.backends.ocr import OCRUnavailable, ocr_engine, read_text


class FakeEngine:
    """Callable stand-in for RapidOCR that records what it was handed."""

    def __init__(self, payload: object) -> None:
        self.payload = payload
        self.calls: list[object] = []

    def __call__(self, image: object) -> object:
        self.calls.append(image)
        return self.payload


class ExplodingEngine:
    """Engine whose failure must reach the caller unchanged."""

    def __call__(self, image: object) -> object:
        raise RuntimeError("engine down")


def _poly(x0: int, y0: int, x1: int, y1: int) -> list[list[int]]:
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def _blank(width: int = 200, height: int = 200) -> np.ndarray:
    return np.zeros((height, width, 3), dtype=np.uint8)


def test_read_text_normalises_rapidocr_items():
    engine = FakeEngine([
        [_poly(10, 100, 90, 120), "新任务", 0.88],
        [_poly(100, 40, 180, 60), "SOLO", 0.99],
    ])

    items = read_text(_blank(), engine=engine)

    assert [item["text"] for item in items] == ["SOLO", "新任务"]
    assert items[0]["text"] == "SOLO"
    assert items[0]["box"] == [[100, 40], [180, 40], [180, 60], [100, 60]]
    assert items[0]["bbox"] == (100, 40, 80, 20)
    assert items[0]["score"] == pytest.approx(0.99)
    assert items[1]["bbox"] == (10, 100, 80, 20)
    assert items[1]["score"] == pytest.approx(0.88)


def test_read_text_sorts_by_row_then_column():
    engine = FakeEngine([
        [_poly(200, 10, 260, 30), "right", 0.9],
        [_poly(20, 10, 80, 30), "left", 0.9],
        [_poly(20, 60, 80, 80), "second row", 0.9],
    ])

    items = read_text(_blank(), engine=engine)

    assert [item["text"] for item in items] == ["left", "right", "second row"]


def test_read_text_accepts_results_elapse_pair():
    engine = FakeEngine(([[_poly(0, 0, 40, 20), "OK", 0.5]], [0.1, 0.2, 0.3]))

    items = read_text(_blank(), engine=engine)

    assert items == [{
        "text": "OK",
        "box": [[0, 0], [40, 0], [40, 20], [0, 20]],
        "bbox": (0, 0, 40, 20),
        "score": pytest.approx(0.5),
    }]


def test_read_text_drops_empty_text():
    engine = FakeEngine([
        [_poly(0, 0, 10, 10), "", 0.9],
        [_poly(0, 20, 10, 30), "   ", 0.9],
        [_poly(0, 40, 10, 50), "kept", 0.9],
    ])

    items = read_text(_blank(), engine=engine)

    assert [item["text"] for item in items] == ["kept"]


def test_read_text_clamps_score_into_zero_one():
    engine = FakeEngine([
        [_poly(0, 0, 10, 10), "high", 1.7],
        [_poly(0, 20, 10, 30), "low", -0.4],
        [_poly(0, 40, 10, 50), "nan", float("nan")],
    ])

    items = read_text(_blank(), engine=engine)

    assert [item["score"] for item in items] == [1.0, 0.0, 0.0]


def test_read_text_returns_empty_list_for_empty_payloads():
    for payload in (None, [], ([], [0.1, 0.2]), (None, None)):
        engine = FakeEngine(payload)

        assert read_text(_blank(), engine=engine) == []


def test_read_text_drops_malformed_items():
    engine = FakeEngine([
        [_poly(0, 0, 10, 10), "good", 0.9],
        ["not a polygon", "bad box", 0.9],
        [[0, 0], "too short", 0.9],
    ])

    items = read_text(_blank(), engine=engine)

    assert [item["text"] for item in items] == ["good"]


def test_read_text_propagates_engine_exceptions():
    with pytest.raises(RuntimeError, match="engine down"):
        read_text(_blank(), engine=ExplodingEngine())


def test_read_text_accepts_numpy_and_pil_images():
    array_engine = FakeEngine([])
    read_text(_blank(), engine=array_engine)
    assert isinstance(array_engine.calls[0], np.ndarray)

    image = Image.new("RGB", (60, 40), (10, 20, 30))
    pil_engine = FakeEngine([])
    read_text(image, engine=pil_engine)
    assert pil_engine.calls[0] is image


def test_ocr_engine_returns_the_injected_engine_unchanged():
    engine = FakeEngine([])

    assert ocr_engine(engine) is engine


def test_ocr_engine_loads_lazily_and_caches_the_engine(monkeypatch):
    built: list[object] = []

    class FakeRapidOCR:
        def __init__(self) -> None:
            built.append(self)

        def __call__(self, image: object) -> object:
            return None

    module = types.ModuleType("rapidocr_onnxruntime")
    module.RapidOCR = FakeRapidOCR
    monkeypatch.setitem(sys.modules, "rapidocr_onnxruntime", module)
    monkeypatch.setattr(ocr_backend, "_ENGINE", None)

    first = ocr_engine()
    second = ocr_engine()

    assert first is second
    assert len(built) == 1


def test_ocr_engine_raises_when_the_dependency_is_missing(monkeypatch):
    monkeypatch.setattr(ocr_backend, "_ENGINE", None)
    monkeypatch.setitem(sys.modules, "rapidocr_onnxruntime", None)

    with pytest.raises(OCRUnavailable) as excinfo:
        ocr_engine()

    assert "rapidocr_onnxruntime" in str(excinfo.value)


def test_ocr_engine_raises_when_the_engine_fails_to_load(monkeypatch):
    class BrokenRapidOCR:
        def __init__(self) -> None:
            raise OSError("onnxruntime model file missing")

    module = types.ModuleType("rapidocr_onnxruntime")
    module.RapidOCR = BrokenRapidOCR
    monkeypatch.setitem(sys.modules, "rapidocr_onnxruntime", module)
    monkeypatch.setattr(ocr_backend, "_ENGINE", None)

    with pytest.raises(OCRUnavailable) as excinfo:
        ocr_engine()

    assert "onnxruntime model file missing" in str(excinfo.value)


def test_rapidocr_adapter_reverses_numpy_channels_for_rapidocr():
    adapter = ocr_backend.RapidOCREngine(lambda image: image)
    array = np.zeros((2, 2, 3), dtype=np.uint8)
    array[..., 0] = 10
    array[..., 1] = 20
    array[..., 2] = 30

    prepared = adapter(array)

    assert prepared[0, 0].tolist() == [30, 20, 10]


def test_rapidocr_adapter_passes_pil_images_through():
    adapter = ocr_backend.RapidOCREngine(lambda image: image)
    image = Image.new("RGB", (2, 2))

    assert adapter(image) is image
