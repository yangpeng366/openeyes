"""Tests for the direct MCP stdio browser_click acceptance probe."""
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "examples" / "browser-click-acceptance.py"


def test_direct_acceptance_boots_mcp_and_keeps_click_dry_run():
    text = PROBE.read_text(encoding="utf-8")
    assert "openeyes.mcp.server" in text
    assert "notifications/initialized" in text
    assert "browser_tabs" in text
    assert "browser_click" in text
    assert '"url_contains": TARGET_MARKER' in text
    assert '"name_contains": "Learn more"' in text
    assert "no page target matched url_contains" in text
    assert '"clicked": False' not in text
    assert '"go": True' not in text
    assert "3080" not in text


def test_direct_acceptance_distinguishes_target_and_missing_markers():
    text = PROBE.read_text(encoding="utf-8")
    assert 'TARGET_MARKER = "target-a"' in text
    assert '"url_contains": "missing-target"' in text
    assert "got targets=" in text
    assert "matched call was not a dry-run" in text
    assert "missing call did not fail closed" in text
