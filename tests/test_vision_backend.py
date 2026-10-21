"""Tests for the parsing-style vision backend.

The vision tier is deliberately *parsing-style*: pixels become structured
elements before anything else touches them. These tests lock that contract
without requiring a model or a network.
"""
from __future__ import annotations

import json

import pytest

from openeyes.backends.vision import (
    OmniParserBackend,
    VisionUnavailableError,
    elements_from_detections,
)

OMNIPARSER_SAMPLE = [
    [[10, 20, 110, 60], "button"],
    [[10, 80, 210, 120], "text"],
    [[300, 20, 340, 60], "icon"],
]


def test_parses_omniparser_native_shape():
    elements = elements_from_detections(OMNIPARSER_SAMPLE)
    assert [e.control_type for e in elements] == ["Button", "Text", "Button"]
    assert elements[0].backend == "vision"
    assert elements[0].bbox.to_dict() == {"x": 10, "y": 20, "w": 100, "h": 40}
    assert elements[0].center.to_dict() == {"x": 60, "y": 40}
    assert elements[0].class_name == "vision:button"


def test_only_actionable_labels_are_interactive():
    elements = elements_from_detections(OMNIPARSER_SAMPLE)
    assert elements[0].interactive is True
    assert elements[1].interactive is False


def test_origin_offsets_image_pixels_to_window_coords():
    elements = elements_from_detections(
        [[[10, 20, 110, 60], "button"]], origin=(100, 50))
    assert elements[0].bbox.to_dict() == {"x": 110, "y": 70, "w": 100, "h": 40}
    assert elements[0].center.to_dict() == {"x": 160, "y": 90}


def test_numeric_label_ids_use_the_id2label_table():
    elements = elements_from_detections([[[0, 0, 10, 10], 4]], id2label={4: "button"})
    assert elements[0].control_type == "Button"
    assert elements[0].name == "button"


def test_default_id2label_is_used_when_no_table_given():
    elements = elements_from_detections([[[0, 0, 10, 10], 4]])
    assert elements[0].control_type == "Button"


def test_score_is_captured_and_clamped():
    scored = elements_from_detections([[[0, 0, 10, 10], "button", 0.42]])
    assert scored[0].score == pytest.approx(0.42)
    high = elements_from_detections([[[0, 0, 10, 10], "button", 1.7]])
    assert high[0].score == 1.0


def test_default_score_stays_below_one():
    elements = elements_from_detections([[[0, 0, 10, 10], "button"]])
    assert elements[0].score < 1.0


def test_boxes_labels_scores_dict_shape():
    payload = {
        "boxes": [[0, 0, 10, 10], [20, 20, 40, 40]],
        "labels": ["button", "link"],
        "scores": [0.9, 0.7],
    }
    elements = elements_from_detections(payload)
    assert [e.control_type for e in elements] == ["Button", "Hyperlink"]
    assert elements[1].score == pytest.approx(0.7)


def test_detections_key_is_recursed_into():
    elements = elements_from_detections({"detections": OMNIPARSER_SAMPLE})
    assert len(elements) == 3


def test_per_item_dict_shape():
    elements = elements_from_detections(
        [{"bbox": [0, 0, 10, 10], "label": "button", "score": 0.5}])
    assert elements[0].control_type == "Button"
    assert elements[0].score == pytest.approx(0.5)


def test_xywh_format_is_supported():
    elements = elements_from_detections(
        [[[10, 20, 100, 40], "button"]], bbox_format="xywh")
    assert elements[0].bbox.to_dict() == {"x": 10, "y": 20, "w": 100, "h": 40}


def test_unusable_boxes_are_skipped_not_fatal():
    payload = [
        [[0, 0, 0, 0], "button"],
        [[0, 0, 5], "text"],
        ["nonsense"],
        [[0, 0, 10, 10], "button"],
    ]
    assert len(elements_from_detections(payload)) == 1


def test_empty_payload_returns_empty_list():
    assert elements_from_detections([]) == []
    assert elements_from_detections(None) == []


def test_limit_caps_the_output():
    payload = [[[i, 0, i + 5, 5], "button"] for i in range(10)]
    assert len(elements_from_detections(payload, limit=3)) == 3


def test_unsupported_bbox_format_raises():
    with pytest.raises(ValueError):
        elements_from_detections([], bbox_format="polygon")

def test_backend_file_mode_needs_no_model(tmp_path):
    path = tmp_path / "det.json"
    path.write_text(json.dumps(OMNIPARSER_SAMPLE), encoding="utf-8")
    backend = OmniParserBackend(detections_path=str(path))
    elements = backend.parse("ignored.png")
    assert len(elements) == 3
    assert elements[0].control_type == "Button"


def test_backend_rejects_endpoint_and_path_together():
    with pytest.raises(ValueError):
        OmniParserBackend(endpoint="http://x", detections_path="y.json")


def test_backend_without_any_source_raises():
    with pytest.raises(VisionUnavailableError):
        OmniParserBackend().parse("ignored.png")


def test_backend_endpoint_mode_posts_base64(monkeypatch, tmp_path):
    sent = {}

    def fake_post(self, url, body):
        sent["url"] = url
        sent["body"] = body
        return [[[0, 0, 10, 10], "button"]]

    monkeypatch.setattr(OmniParserBackend, "_post_json", fake_post)
    image = tmp_path / "shot.png"
    image.write_bytes(b"\x89PNG-fake")
    backend = OmniParserBackend(endpoint="http://127.0.0.1:9/parse")
    elements = backend.parse(str(image))
    assert sent["url"] == "http://127.0.0.1:9/parse"
    assert sent["body"]["image"]
    assert elements[0].control_type == "Button"


def test_backend_endpoint_failure_is_wrapped(monkeypatch, tmp_path):
    def boom(self, url, body):
        raise OSError("connection refused")

    monkeypatch.setattr(OmniParserBackend, "_post_json", boom)
    image = tmp_path / "shot.png"
    image.write_bytes(b"x")
    backend = OmniParserBackend(endpoint="http://127.0.0.1:9/parse")
    with pytest.raises(VisionUnavailableError):
        backend.parse(str(image))


def test_backend_file_mode_tolerates_a_bom(tmp_path):
    path = tmp_path / "det.json"
    path.write_text(json.dumps(OMNIPARSER_SAMPLE), encoding="utf-8-sig")
    backend = OmniParserBackend(detections_path=str(path))
    assert len(backend.parse_file(path)) == 3