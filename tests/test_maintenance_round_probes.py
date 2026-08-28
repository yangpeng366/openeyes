"""Contract tests for examples/maintenance-round-probes.ps1.

These tests do not run the live probe set. They lock the default safe
mode and the five deferred-acceptance probes recorded for 2026-09-04.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "examples" / "maintenance-round-probes.ps1"
PROBE_IDS = ["git_state", "pytest_suite", "dsh_preflight", "browser_gate", "skill_hash"]


def test_script_defaults_to_list_mode_and_documents_five_probes():
    text = SCRIPT.read_text(encoding="utf-8")

    assert "[ValidateSet('List','Run')][string]$Mode = 'List'" in text
    assert "'2026-09-04T18:00:00+08:00'" in text
    assert "Run mode requires -ReportPath" in text
    assert "[System.IO.File]::WriteAllText" in text
    assert "[System.Text.UTF8Encoding]::new($false)" in text

    for probe_id in PROBE_IDS:
        assert probe_id in text, f"probe {probe_id!r} missing from script"

    assert "python -m pytest tests/ -q --no-header" in text
    assert "pwsh -NoProfile -File examples\\dsh-preflight.ps1" in text
    assert "Get-NetTCPConnection -State Listen" in text
    assert "Get-FileHash" in text


def test_list_mode_outputs_structured_probe_set():
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if not pwsh:
        pytest.skip("pwsh not available")

    proc = subprocess.run(
        [pwsh, "-NoProfile", "-File", str(SCRIPT), "-Mode", "List"],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["mode"] == "list"
    assert data["scheduled_for"] == "2026-09-04T18:00:00+08:00"
    assert [p["id"] for p in data["probes"]] == PROBE_IDS
    assert "git rev-parse origin/main" in data["probes"][0]["command"]
    assert "python -m pytest tests/" in data["probes"][1]["command"]
