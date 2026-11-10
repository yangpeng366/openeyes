"""The replay report must survive a console that cannot encode its text.

Windows terminals frequently run under GBK while a trajectory may contain
characters such as U+200B. The report is still useful there: unencodable
characters should be replaced, not turned into a traceback.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from openeyes.cli import main


class _GbkStdout:
    encoding = "gbk"


def test_safe_echo_replaces_characters_the_console_cannot_encode(monkeypatch, capsys):
    written: list[str] = []

    def fake_echo(message, err=False):
        if "\u200b" in message:
            raise UnicodeEncodeError("gbk", message, 0, 1, "illegal multibyte sequence")
        written.append(message)

    monkeypatch.setattr(main.sys, "stdout", _GbkStdout())
    monkeypatch.setattr(main.click, "echo", fake_echo)

    main._safe_echo("probe\u200bwindow")

    assert written == ["probe?window"]


def _write_trajectory(path: Path) -> None:
    header = {
        "_kind": "header",
        "id": "00000000-0000-4000-8000-000000000001",
        "name": "encoding probe",
        "started_at": 1.0,
        "ended_at": 2.0,
        "app_name": "probe",
        "description": "",
        "step_count": 1,
    }
    step = {
        "ts": 1.5,
        "action": "click",
        "element": {
            "backend": "uia",
            "control_type": "Pane",
            "name": "",
            "bbox": {"x": 0, "y": 0, "w": 10, "h": 10},
            "center": {"x": 5, "y": 5},
            "automation_id": "",
            "class_name": "Probe",
            "score": 1.0,
            "hint": None,
            "interactive": True,
            "state": {
                "enabled": True,
                "visible": True,
                "focused": False,
                "selected": False,
            },
            "parent_chain": [],
        },
        "selector": "name=probe\u200bwindow|type=Pane",
        "app_name": "probe",
        "window_title": "probe\u200bwindow",
        "hwnd": 0,
        "pid": 0,
        "intent": "",
        "input_text": None,
        "meta": {"button": "left"},
    }
    path.write_text(
        json.dumps(header, ensure_ascii=False) + "\n"
        + json.dumps(step, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def test_record_replay_finishes_on_a_gbk_console(tmp_path):
    trajectory = tmp_path / "gbk.jsonl"
    _write_trajectory(trajectory)
    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "gbk"
    env.pop("PYTHONUTF8", None)

    result = subprocess.run(
        [sys.executable, "-m", "openeyes.cli.main", "record", "replay", str(trajectory)],
        cwd=root,
        env=env,
        capture_output=True,
    )

    stdout = result.stdout.decode("gbk", errors="replace")
    stderr = result.stderr.decode("gbk", errors="replace")
    assert result.returncode == 0, stderr
    assert "1 planned" in stdout
    assert "UnicodeEncodeError" not in stderr
