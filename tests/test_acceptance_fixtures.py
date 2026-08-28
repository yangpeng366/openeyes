"""Contract tests for the browser_click acceptance fixtures.

These verify that the two disposable HTML pages and the tab launcher exist and
have the structure the dsh-web acceptance runbook (section 4) requires. They do
not open tabs or touch a live browser.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PAGES_DIR = ROOT / "examples" / "acceptance-pages"
TARGET_A = PAGES_DIR / "target-a.html"
DECOY = PAGES_DIR / "decoy.html"
LAUNCHER = ROOT / "examples" / "open-acceptance-tabs.py"


def test_target_a_fixture_has_learn_more_link():
    text = TARGET_A.read_text(encoding="utf-8")
    assert "Learn more" in text
    assert "target-a" in text


def test_decoy_fixture_filename_excludes_target_a():
    assert "target-a" not in DECOY.name
    text = DECOY.read_text(encoding="utf-8")
    assert "Learn more" in text


def test_launcher_defaults_to_dry_run_and_references_both_pages():
    text = LAUNCHER.read_text(encoding="utf-8")
    assert 'action="store_true"' in text
    assert '"--go"' in text
    assert "Target.createTarget" in text
    assert "Target.closeTarget" in text
    assert "target-a.html" in text
    assert "decoy.html" in text
    assert "CDP_DEFAULT_PORT = 9222" in text
    assert "dry-run" in text


def test_launcher_dry_run_reports_fixtures_without_opening_tabs():
    py = shutil.which("python")
    if not py:
        pytest.skip("python not available")
    proc = subprocess.run(
        [py, str(LAUNCHER)],
        capture_output=True, text=True, timeout=15,
    )
    assert proc.returncode == 0, proc.stderr
    assert "acceptance fixtures:" in proc.stdout
    assert "target-a" in proc.stdout
    assert "decoy" in proc.stdout
    assert "dry-run" in proc.stdout