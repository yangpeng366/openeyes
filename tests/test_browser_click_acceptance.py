"""Tests for the direct MCP stdio browser_click acceptance probe."""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "examples" / "browser-click-acceptance.py"
RUNBOOK = ROOT / "docs" / "dsh-web-acceptance.md"


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


def test_result_content_parses_single_text_payload():
    spec = importlib.util.spec_from_file_location(
        "browser_click_acceptance", PROBE,
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    result = {"content": [{"type": "text", "text": json.dumps({"ok": True})}]}
    assert module._result_content(result) == {"ok": True}


def test_dsh_runbook_section_4_keeps_both_calls_dry_run():
    runbook = RUNBOOK.read_text(encoding="utf-8")
    section = runbook.split("## 4. Run the two-tab click check", 1)[1]
    section = section.split("## Pass criteria", 1)[0]
    payloads = [
        json.loads(block)
        for block in re.findall(r"```json\n(.*?)\n```", section, re.DOTALL)
        if "browser_click" in block
    ]

    assert len(payloads) == 2
    assert [p["arguments"]["url_contains"] for p in payloads] == [
        "target-a", "missing-target",
    ]
    assert all(p["name"] == "browser_click" for p in payloads)
    assert all("go" not in p["arguments"] for p in payloads)
    assert "clicked:false" in runbook
    assert "would_click:true" in runbook
    assert "no page target matched url_contains" in runbook
