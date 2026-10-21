"""CLI surface for the vision tier and the degradation chain."""
from __future__ import annotations

import json

from click.testing import CliRunner

from openeyes.cli.main import cli

SAMPLE = [
    [[10, 20, 110, 60], "button"],
    [[10, 80, 210, 120], "text"],
]


def _write_detections(tmp_path):
    path = tmp_path / "det.json"
    path.write_text(json.dumps(SAMPLE), encoding="utf-8")
    return path


def test_vision_command_converts_detections(tmp_path):
    path = _write_detections(tmp_path)
    result = CliRunner().invoke(cli, ["vision", "--detections", str(path)])
    assert result.exit_code == 0, result.output
    elements = json.loads(result.output)
    assert elements[0]["control_type"] == "Button"
    assert elements[0]["backend"] == "vision"
    assert elements[0]["center"] == {"x": 60, "y": 40}


def test_vision_command_honours_the_origin_offset(tmp_path):
    path = _write_detections(tmp_path)
    result = CliRunner().invoke(
        cli, ["vision", "--detections", str(path), "--origin-x", "100", "--origin-y", "50"])
    assert result.exit_code == 0, result.output
    elements = json.loads(result.output)
    assert elements[0]["bbox"] == {"x": 110, "y": 70, "w": 100, "h": 40}


def test_resolve_command_reports_which_tier_answered(tmp_path):
    path = _write_detections(tmp_path)
    result = CliRunner().invoke(cli, [
        "resolve", "--window", "0", "--port", "0",
        "--vision-detections", str(path),
    ])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["used_backend"] == "vision"
    assert [a["backend"] for a in payload["attempts"]] == ["uia", "cdp", "vision"]
    assert payload["attempts"][0]["ok"] is False
    assert payload["attempts"][0]["reason"]
    assert payload["elements"][0]["backend"] == "vision"