from __future__ import annotations

import json
import subprocess

import pytest

from examples.verify_record_schema import verify
from openeyes.record import InputEvent, Trajectory


def _save_trajectory(path, action_count):
    trajectory = Trajectory.new(path.stem)
    for index in range(action_count):
        trajectory.add(
            InputEvent(
                ts=1.0 + index,
                action="wait",
                duration_ms=100,
            )
        )
    trajectory.save(str(path))


def test_verify_reports_step_count_mismatch(tmp_path, monkeypatch):
    uia_path = tmp_path / "trajectory.uia.jsonl"
    edge_path = tmp_path / "trajectory.edge.jsonl"
    _save_trajectory(uia_path, 1)
    _save_trajectory(edge_path, 2)

    def fake_node_parse(path):
        step_count = 1 if "uia" in path.name else 2
        return {
            "step_count": step_count,
            "actual": step_count,
            "actions": ["wait"] * step_count,
        }

    monkeypatch.setattr("examples.verify_record_schema._node_parse", fake_node_parse)
    report_path = tmp_path / "report.json"

    with pytest.raises(AssertionError, match="trajectory alignment failed"):
        verify(uia_path, edge_path, report_path)

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["node_parse_ok"] is True
    assert report["alignment"]["step_count_equal"] is False
    assert report["alignment"]["action_sequence_equal"] is False
    assert report["alignment"]["python_to_node"] is True


def test_verify_reports_node_parse_failure(tmp_path, monkeypatch):
    uia_path = tmp_path / "trajectory.uia.jsonl"
    edge_path = tmp_path / "trajectory.edge.jsonl"
    _save_trajectory(uia_path, 1)
    _save_trajectory(edge_path, 1)

    def failing_node_parse(path):
        raise subprocess.CalledProcessError(1, "node", stderr="bridge parse failed")

    monkeypatch.setattr(
        "examples.verify_record_schema._node_parse",
        failing_node_parse,
    )
    report_path = tmp_path / "report.json"

    with pytest.raises(AssertionError, match="trajectory alignment failed"):
        verify(uia_path, edge_path, report_path)

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["node_parse_ok"] is False
    assert report["alignment"]["python_to_node"] is False
    assert report["uia"]["canonical_node"]["error"]
    assert report["edge"]["original_node"]["error"]
