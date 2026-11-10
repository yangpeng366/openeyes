"""`eyes record replay --live` is opt-in, and its exit code follows failures.

Every test here replaces ``openeyes.record.replayer.Replayer`` with a fake
that never touches the desktop, so no mouse or keyboard input is ever sent.
"""
from __future__ import annotations

import json

from click.testing import CliRunner

from openeyes.cli.main import cli
from openeyes.record import InputEvent, Trajectory
from openeyes.record.replayer import ReplayReport


def _write_trajectory(tmp_path) -> tuple[str, Trajectory]:
    trajectory = Trajectory.new("live-replay")
    trajectory.add(InputEvent(ts=1.0, action="hotkey", combo="ctrl+l", duration_ms=0))
    trajectory.add(InputEvent(ts=2.0, action="wait", duration_ms=120))
    path = tmp_path / "trajectory.jsonl"
    trajectory.save(str(path))
    return str(path), trajectory


def _report(trajectory: Trajectory, dry_run: bool, statuses: list[str]) -> ReplayReport:
    """A ReplayReport with one action per status, shaped like the real one."""
    report = ReplayReport(
        trajectory_id=trajectory.id,
        trajectory_name=trajectory.name,
        dry_run=dry_run,
    )
    for index, status in enumerate(statuses):
        report.actions.append({
            "index": index,
            "action": "click",
            "plan": {"kind": "click", "selector": "automation_id=btn_submit"},
            "status": status,
            "expectation": None,
            "expectation_source": "no expectation recorded",
            "path": "selector",
            "paths": ["selector"],
            "attempts": 1,
            "elapsed": 0.01,
            "last_observed": "probe present=False",
            "preexisting": False,
            "sent": not dry_run,
            "text_typed": False,
            "reason": "expected state not observed within timeout"
            if status == "failed" else "no expectation recorded",
            "error": "expected state not observed within timeout"
            if status == "failed" else None,
        })
    return report


def _patch_replayer(monkeypatch, dialog):
    """Replace the Replayer the CLI imports, recording the dry_run it got."""
    seen: dict[str, object] = {}

    class _FakeReplayer:
        def __init__(self, dry_run: bool = True, **kwargs) -> None:
            seen["dry_run"] = dry_run
            seen["kwargs"] = kwargs

        def replay(self, trajectory: Trajectory) -> ReplayReport:
            statuses = dialog["statuses"]
            if statuses is None:
                statuses = ["planned"] * len(trajectory.steps) if seen["dry_run"] \
                    else ["verified"] * len(trajectory.steps)
            return _report(trajectory, bool(seen["dry_run"]), statuses)

    monkeypatch.setattr("openeyes.record.replayer.Replayer", _FakeReplayer)
    return seen


# --------------------------------------------------------------- opt-in only
def test_without_live_the_cli_stays_a_dry_run(tmp_path, monkeypatch):
    path, trajectory = _write_trajectory(tmp_path)
    seen = _patch_replayer(monkeypatch, {"statuses": None})

    result = CliRunner().invoke(cli, ["record", "replay", path])

    assert result.exit_code == 0, result.output
    assert seen["dry_run"] is True
    assert trajectory.steps  # a real trajectory was planned
    assert "2 planned, 0 skipped, 0 errors" in result.output
    assert "verified" not in result.output


def test_live_flag_turns_the_dry_run_off(tmp_path, monkeypatch):
    path, _ = _write_trajectory(tmp_path)
    seen = _patch_replayer(monkeypatch, {"statuses": None})

    result = CliRunner().invoke(cli, ["record", "replay", path, "--live"])

    assert result.exit_code == 0, result.output
    assert seen["dry_run"] is False
    assert "2 verified / 0 unverified / 0 failed" in result.output


def test_live_prints_one_line_per_step_with_the_status(tmp_path, monkeypatch):
    path, _ = _write_trajectory(tmp_path)
    _patch_replayer(monkeypatch, {"statuses": ["verified", "unverified"]})

    result = CliRunner().invoke(cli, ["record", "replay", path, "--live"])

    assert result.exit_code == 0, result.output
    assert "0: verified" in result.output
    assert "1: unverified" in result.output


# ------------------------------------------------------------ failing steps
def test_a_failed_step_exits_non_zero_and_is_summarised(tmp_path, monkeypatch):
    path, _ = _write_trajectory(tmp_path)
    _patch_replayer(monkeypatch, {"statuses": ["verified", "failed"]})

    result = CliRunner().invoke(cli, ["record", "replay", path, "--live"])

    assert result.exit_code != 0
    assert "1 verified / 0 unverified / 1 failed" in result.output
    assert "1: failed" in result.output
    assert "error:" in result.stderr


def test_a_failed_step_also_exits_non_zero_in_json_mode(tmp_path, monkeypatch):
    path, _ = _write_trajectory(tmp_path)
    _patch_replayer(monkeypatch, {"statuses": ["failed"]})

    result = CliRunner().invoke(cli, ["record", "replay", path, "--live", "--json"])

    assert result.exit_code != 0
    report = json.loads(result.stdout)
    assert report["summary"]["failed"] == 1


def test_missing_trajectory_is_still_a_usage_error(tmp_path):
    result = CliRunner().invoke(
        cli, ["record", "replay", str(tmp_path / "missing.jsonl"), "--live"],
    )

    assert result.exit_code == 2
    assert "does not exist" in result.output


# ----------------------------------------------------------------- json shape
def test_json_output_is_parseable_in_both_modes(tmp_path, monkeypatch):
    path, trajectory = _write_trajectory(tmp_path)
    seen = _patch_replayer(monkeypatch, {"statuses": None})

    dry = CliRunner().invoke(cli, ["record", "replay", path, "--json"])
    assert dry.exit_code == 0, dry.output
    assert seen["dry_run"] is True
    dry_report = json.loads(dry.output)
    assert dry_report["dry_run"] is True
    assert dry_report["trajectory_id"] == trajectory.id
    assert [item["status"] for item in dry_report["actions"]] == ["planned", "planned"]
    assert dry_report["summary"]["planned"] == 2

    live = CliRunner().invoke(cli, ["record", "replay", path, "--live", "--json"])
    assert live.exit_code == 0, live.output
    assert seen["dry_run"] is False
    live_report = json.loads(live.output)
    assert live_report["dry_run"] is False
    for item in live_report["actions"]:
        assert {"index", "action", "status", "reason", "sent"} <= set(item)
    assert live_report["summary"]["verified"] == 2
    assert live_report["summary"]["all_effect_verified"] is True
