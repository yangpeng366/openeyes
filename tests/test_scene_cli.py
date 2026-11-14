"""CLI and MCP surface of the scene text command.

The scene itself is covered by ``tests/test_scene.py``; here we only care
that ``eyes scene`` reads a window and prints a digest, and that it never
turns a description into an action.
"""
from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from openeyes.cli.main import cli
from openeyes.core import scene as scene_api

SCENE = {
    "ok": True,
    "source": "ocr",
    "hwnd": 4242,
    "window": {"title": "Trae CN", "size": [1886, 1183]},
    "elements": [
        {"kind": "button", "text": "新任务", "bbox": {"x": 24, "y": 143, "w": 106, "h": 48},
         "center": {"x": 77, "y": 167}, "score": 0.99},
        {"kind": "label", "text": "SOLO", "bbox": {"x": 22, "y": 84, "w": 51, "h": 28},
         "center": {"x": 47, "y": 98}, "score": 1.0},
    ],
    "text_lines": ["新任务", "SOLO"],
    "shapes": [],
    "counts": {"button": 1, "label": 1},
    "elapsed_ms": 12,
    "error_code": None,
    "notes": [],
}


@pytest.fixture()
def fake_scene(monkeypatch):
    def fake_describe(hwnd, **kwargs):
        assert hwnd == 4242
        return dict(SCENE)

    monkeypatch.setattr(scene_api, "describe_window", fake_describe)


def test_scene_prints_a_text_digest_by_default(fake_scene):
    result = CliRunner().invoke(cli, ["scene", "--window", "4242"])

    assert result.exit_code == 0, result.output
    assert "窗口 Trae CN 1886x1183" in result.output
    assert "[按钮] 新任务 @(77,167) 0.99" in result.output


def test_scene_json_carries_the_digest(fake_scene):
    result = CliRunner().invoke(cli, ["scene", "--window", "4242", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["text_digest"].startswith("窗口 Trae CN")
    assert payload["elements"][0]["text"] == "新任务"


def test_scene_reports_a_failure_with_a_defined_exit_code(monkeypatch):
    monkeypatch.setattr(
        scene_api, "describe_window",
        lambda hwnd, **kwargs: {"ok": False, "error_code": "OCR_UNAVAILABLE",
                                "elements": [], "window": {}},
    )

    result = CliRunner().invoke(cli, ["scene", "--window", "4242", "--json"])

    assert result.exit_code == 4
    assert "OCR_UNAVAILABLE" in result.output
