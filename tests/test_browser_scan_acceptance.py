"""Tests for the direct MCP stdio browser_scan acceptance probe."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "examples" / "browser-scan-acceptance.py"


def test_scan_acceptance_boots_mcp_and_keeps_scan_read_only():
    text = PROBE.read_text(encoding="utf-8")
    assert "openeyes.mcp.server" in text
    assert "notifications/initialized" in text
    assert "browser_tabs" in text
    assert "browser_scan" in text
    assert '"url_contains": target_filter' in text
    assert '"url_contains": decoy_filter' in text
    assert '"url_contains": missing_filter' in text
    assert '"name_contains": "Secondary action"' in text
    assert "no page target matched url_contains" in text
    # browser_scan is read-only; the probe must never force a write/side effect.
    assert '"go": True' not in text
    assert '"dry_run": False' not in text
    assert "--cdp-port" in text
    assert '[sys.executable, "-m", "openeyes.mcp.server"]' in text
    assert '"port": args.cdp_port' in text


def test_scan_acceptance_distinguishes_target_decoy_and_missing_markers():
    text = PROBE.read_text(encoding="utf-8")
    assert 'TARGET_MARKER = "target-a"' in text
    assert 'DECOY_MARKER = "decoy"' in text
    assert 'target_filter = f"{TARGET_MARKER}.html?probe={probe_id}"' in text
    assert 'decoy_filter = f"{DECOY_MARKER}.html?probe={probe_id}"' in text
    assert 'missing_filter = f"missing-{probe_id}"' in text
    assert "expected at least one target-a and one decoy acceptance" in text
    assert "matched scan did not isolate the target-a Secondary action" in text
    assert "decoy scan did not isolate the decoy Learn more link" in text
    assert "missing call did not fail closed" in text
    assert "missing call returned elements" in text


def test_scan_acceptance_tolerates_duplicate_acceptance_tabs():
    text = PROBE.read_text(encoding="utf-8")
    assert "len(target_tabs) < 1" in text
    assert "len(decoy_tabs) < 1" in text
    assert "target_tabs[:1]" in text
    assert "decoy_tabs[:1]" in text
    assert "len(target_tabs) != 1" not in text


def test_scan_acceptance_result_content_parses_single_text_payload():
    spec = importlib.util.spec_from_file_location(
        "browser_scan_acceptance", PROBE,
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    result = {"content": [{"type": "text", "text": json.dumps({"ok": True})}]}
    assert module._result_content(result) == {"ok": True}


def test_scan_acceptance_helper_classifies_payload_names():
    spec = importlib.util.spec_from_file_location(
        "browser_scan_acceptance", PROBE,
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    assert module._scan_element_names(
        [{"name": "Learn more"}, {"name": "Secondary action"}]
    ) == ["Learn more", "Secondary action"]
    import pytest
    with pytest.raises(RuntimeError):
        module._scan_element_names({"error": "boom"})
