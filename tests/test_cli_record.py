from __future__ import annotations

import json

from click.testing import CliRunner

from openeyes.cli.main import cli
from openeyes.record import InputEvent, Trajectory


def _write_trajectory(path) -> Trajectory:
    trajectory = Trajectory.new("CLI replay")
    trajectory.add(InputEvent(ts=1.0, action="hotkey", combo="ctrl+l", duration_ms=0))
    trajectory.add(InputEvent(ts=2.0, action="wait", duration_ms=120))
    trajectory.save(str(path))
    return trajectory


def test_record_replay_dry_run_json(tmp_path):
    path = tmp_path / "trajectory.jsonl"
    trajectory = _write_trajectory(path)

    result = CliRunner().invoke(cli, ["record", "replay", str(path), "--json"])

    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["dry_run"] is True
    assert report["trajectory_id"] == trajectory.id
    assert [item["plan"]["kind"] for item in report["actions"]] == ["hotkey", "wait"]
    assert report["skipped_count"] == 0
    assert report["errors"] == []


def test_record_replay_missing_file_fails(tmp_path):
    result = CliRunner().invoke(cli, ["record", "replay", str(tmp_path / "missing.jsonl")])

    assert result.exit_code != 0
    assert "does not exist" in result.output
