"""Tests for the direct MCP stdio browser_shot acceptance probe."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "examples" / "browser-shot-acceptance.py"


def test_shot_acceptance_boots_mcp_and_keeps_shot_dry_run():
    text = PROBE.read_text(encoding="utf-8")
    assert "openeyes.mcp.server" in text
    assert "notifications/initialized" in text
    assert "browser_tabs" in text
    assert "browser_shot" in text
    assert '"url_contains": TARGET_MARKER' in text
    assert "no page target matched url_contains" in text
    # Shot is a write because it creates a PNG; the probe must rely on the
    # tool's dry-run default and never force execution.
    assert '"dry_run": False' not in text
    assert "3080" not in text


def test_shot_acceptance_distinguishes_target_and_missing_markers():
    text = PROBE.read_text(encoding="utf-8")
    assert 'TARGET_MARKER = "target-a"' in text
    assert '"url_contains": "missing-target"' in text
    assert "expected at least one" in text
    assert "matched call was not a dry-run" in text
    assert "matched call resolved the wrong target" in text
    assert "missing call did not fail closed" in text


def test_shot_acceptance_tolerates_duplicate_acceptance_tabs():
    text = PROBE.read_text(encoding="utf-8")
    assert "len(target_tabs) < 1" in text
    assert "len(decoy_tabs) < 1" in text
    assert "target_tabs[:1]" in text
    assert "decoy_tabs[:1]" in text
    assert "len(target_tabs) != 1" not in text


def test_shot_acceptance_result_content_parses_single_text_payload():
    spec = importlib.util.spec_from_file_location(
        "browser_shot_acceptance", PROBE,
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    result = {"content": [{"type": "text", "text": json.dumps({"ok": True})}]}
    assert module._result_content(result) == {"ok": True}
