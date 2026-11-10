"""Tests for `eyes record export` — packaging a recording as a loadable skill.

Covers:
- the exported folder holds SKILL.md plus a byte-identical trajectory.jsonl
- SKILL.md front-matter parses (name / description) and names the run entry
- replaying the exported trajectory reproduces the source step count
- a non-empty target directory fails without --force and succeeds with it
- paths containing spaces and Chinese characters

No real UI is touched: every step is planned by the dry-run replayer.
"""
from __future__ import annotations

import json

from click.testing import CliRunner

from openeyes.cli.main import cli
from openeyes.record import InputEvent, Trajectory, UIAEvent
from openeyes.record.replayer import Replayer

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _fake_element(**overrides):
    """Minimal OpenEyes Element.to_dict() for a replay plan."""
    base = {
        "backend": "uia",
        "control_type": "Button",
        "name": "Submit",
        "bbox": {"x": 100, "y": 200, "w": 80, "h": 30},
        "center": {"x": 140, "y": 215},
        "automation_id": "btn_submit",
        "class_name": "Button",
        "score": 1.0,
        "hint": "a",
        "interactive": True,
        "state": {"enabled": True, "visible": True},
        "parent_chain": [],
    }
    base.update(overrides)
    return base


def _source_trajectory() -> Trajectory:
    trajectory = Trajectory.new(
        "Approve Invoice", app_name="Notepad", description="Four steps, no vision.",
    )
    trajectory.add(UIAEvent(
        ts=1.0, action="click", element=_fake_element(),
        selector="automation_id=btn_submit", intent="submit the form",
    ))
    trajectory.add(UIAEvent(
        ts=2.0, action="type", element=_fake_element(control_type="Edit", name="Body"),
        selector="name=Body|type=Edit", intent="fill the body", input_text="hello",
    ))
    trajectory.add(InputEvent(ts=3.0, action="hotkey", combo="ctrl+s", intent="save"))
    trajectory.add(InputEvent(ts=4.0, action="wait", duration_ms=200, intent="settle"))
    trajectory.close()
    return trajectory


def _write_source(tmp_path, name: str = "trajectory.jsonl"):
    path = tmp_path / name
    trajectory = _source_trajectory()
    trajectory.save(str(path))
    return path, trajectory


def _front_matter(text: str) -> tuple[dict, str]:
    """Split a SKILL.md into (front-matter map, body); no YAML dependency."""
    assert text.startswith("---\n"), text[:40]
    _, raw, body = text.split("---\n", 2)
    fields = {}
    for line in raw.splitlines():
        key, sep, value = line.partition(":")
        assert sep, line
        fields[key.strip()] = value.strip().strip('"')
    return fields, body


def _export(source, skill_dir, *extra: str):
    return CliRunner().invoke(cli, [
        "record", "export", str(source), "--skill-dir", str(skill_dir), *extra,
    ])


# ---------------------------------------------------------------------------
# Structure
# ---------------------------------------------------------------------------

def test_export_writes_skill_md_and_byte_identical_trajectory(tmp_path):
    source, _ = _write_source(tmp_path)
    skill_dir = tmp_path / "skill"

    result = _export(source, skill_dir, "--json")

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["skill_dir"] == str(skill_dir.resolve())
    assert payload["steps"] == 4
    assert payload["skill_md"] == str(skill_dir.resolve() / "SKILL.md")
    assert payload["trajectory"] == str(skill_dir.resolve() / "trajectory.jsonl")
    # The copy is raw bytes, not a re-serialisation.
    assert (skill_dir / "trajectory.jsonl").read_bytes() == source.read_bytes()

    meta, body = _front_matter((skill_dir / "SKILL.md").read_text(encoding="utf-8"))
    assert meta["name"] == payload["name"]
    assert meta["name"].startswith("openeyes-record-")
    assert meta["description"]  # front-matter parses with a real description
    assert "| `click` |" in body and "| `wait` |" in body


def test_skill_body_names_the_run_entry_and_defaults_to_dry_run(tmp_path):
    source, _ = _write_source(tmp_path)
    skill_dir = tmp_path / "skill"

    result = _export(source, skill_dir, "--description", "Submit an invoice in Notepad.")

    assert result.exit_code == 0, result.output
    meta, body = _front_matter((skill_dir / "SKILL.md").read_text(encoding="utf-8"))
    assert meta["description"] == "Submit an invoice in Notepad."
    assert "## Run it" in body
    assert "eyes record replay" in body
    assert "trajectory.jsonl" in body
    assert "dry-run" in body
    # One table row per recorded step.
    rows = [line for line in body.splitlines() if line.startswith("| ")
            and line.split("|")[1].strip().isdigit()]
    assert len(rows) == 4
    assert "error: " not in result.output


# ---------------------------------------------------------------------------
# Closed loop: record -> export -> replay the export
# ---------------------------------------------------------------------------

def test_exported_trajectory_replays_to_the_source_step_count(tmp_path):
    source, trajectory = _write_source(tmp_path)
    skill_dir = tmp_path / "skill"
    payload = json.loads(_export(source, skill_dir, "--json").output)

    replayed = Replayer(dry_run=True).replay(Trajectory.load(payload["trajectory"]))

    assert len(replayed.actions) == len(trajectory.steps)
    assert replayed.skipped == []
    assert replayed.errors == []
    assert replayed.trajectory_id == trajectory.id

    # The CLI replay path sees the same steps as the Python one.
    result = CliRunner().invoke(cli, ["record", "replay", payload["trajectory"], "--json"])
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["summary"]["total"] == payload["steps"] == 4
    assert report["dry_run"] is True


# ---------------------------------------------------------------------------
# Non-empty target: refuse, then --force
# ---------------------------------------------------------------------------

def test_non_empty_skill_dir_fails_without_force(tmp_path):
    source, _ = _write_source(tmp_path)
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    (skill_dir / "NOTES.txt").write_text("mine", encoding="utf-8")

    result = _export(source, skill_dir, "--json")

    assert result.exit_code != 0
    assert "not empty" in result.output
    assert "--force" in result.output
    assert not (skill_dir / "SKILL.md").exists()
    assert not (skill_dir / "trajectory.jsonl").exists()
    assert (skill_dir / "NOTES.txt").read_text(encoding="utf-8") == "mine"


def test_force_overwrites_the_two_exported_files_only(tmp_path):
    source, _ = _write_source(tmp_path)
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("stale skill\n", encoding="utf-8")
    (skill_dir / "trajectory.jsonl").write_text("{}\n", encoding="utf-8")
    (skill_dir / "NOTES.txt").write_text("mine", encoding="utf-8")

    result = _export(source, skill_dir, "--force", "--json")

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["steps"] == 4
    meta, _ = _front_matter((skill_dir / "SKILL.md").read_text(encoding="utf-8"))
    assert meta["name"] == payload["name"]
    assert (skill_dir / "trajectory.jsonl").read_bytes() == source.read_bytes()
    assert (skill_dir / "NOTES.txt").read_text(encoding="utf-8") == "mine"


# ---------------------------------------------------------------------------
# Awkward paths
# ---------------------------------------------------------------------------

def test_export_handles_spaces_and_chinese_in_paths(tmp_path):
    root = tmp_path / "导出 目录" / "my skill dir"
    root.mkdir(parents=True)
    source, trajectory = _write_source(root, "记录 01.jsonl")
    skill_dir = root / "技能 包"

    result = _export(source, skill_dir, "--description", "含中文与空格", "--json")

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["steps"] == len(trajectory.steps)
    assert "技能 包" in payload["skill_dir"]
    assert (skill_dir / "SKILL.md").is_file()
    assert (skill_dir / "trajectory.jsonl").read_bytes() == source.read_bytes()
    meta, body = _front_matter((skill_dir / "SKILL.md").read_text(encoding="utf-8"))
    assert meta["description"] == "含中文与空格"
    assert payload["trajectory"] in body

    replayed = Replayer(dry_run=True).replay(Trajectory.load(payload["trajectory"]))
    assert len(replayed.actions) == payload["steps"]
