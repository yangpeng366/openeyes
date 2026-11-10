"""Behavioural tests for the direct MCP stdio browser_scan acceptance probe.

Earlier revisions asserted that certain sentences existed in the probe source.
A probe whose filter silently widened (``in`` instead of ``==``), whose empty
result was accepted, or whose fail-closed branch returned elements still
printed ``passed: true`` and these tests stayed green. They now call the
probe's own contract functions with realistic MCP payloads and assert the
values that decide pass/fail.
"""
from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "examples" / "browser-scan-acceptance.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("browser_scan_acceptance", PROBE)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _element(name: str, *, control_type: str = "Hyperlink", x: int = 120, y: int = 240) -> dict:
    """One element exactly as ``browser_scan`` serialises it."""
    return {
        "backend": "cdp",
        "control_type": control_type,
        "name": name,
        "automation_id": "",
        "class_name": "",
        "bbox": {"x": x, "y": y, "w": 96, "h": 20},
        "center": {"x": x + 48, "y": y + 10},
        "score": 1.0,
        "hint": "J",
        "interactive": True,
        "state": {"enabled": True, "visible": True, "focused": False, "selected": False},
        "parent_chain": ["RootWebArea", "body"],
    }


def test_scan_contract_accepts_a_single_isolated_element():
    module = _load_module()

    names = module._require_exact_scan_names(
        [_element("Secondary action")],
        "Secondary action",
        label="matched scan",
    )

    assert names == ["Secondary action"]


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param([_element("Secondary action"), _element("Learn more")], id="decoy-leak"),
        pytest.param([_element("Secondary actions")], id="near-miss-name"),
        pytest.param([], id="empty-result"),
    ],
)
def test_scan_contract_rejects_anything_but_the_one_expected_name(payload):
    """`in`-comparisons, dropped empty checks and widened filters must all fail."""
    module = _load_module()

    with pytest.raises(RuntimeError) as excinfo:
        module._require_exact_scan_names(payload, "Secondary action", label="matched scan")

    assert "matched scan" in str(excinfo.value)
    assert "Secondary action" in str(excinfo.value)


def test_scan_contract_rejects_a_stub_success_payload():
    """A probe replaced by `{"ok": true}` must not read as a passing scan."""
    module = _load_module()

    with pytest.raises(RuntimeError) as excinfo:
        module._require_exact_scan_names({"ok": True}, "Secondary action", label="matched scan")

    assert "non-list payload" in str(excinfo.value)


def test_scan_contract_reports_the_names_it_actually_saw():
    """A failing run must name the elements, otherwise the evidence is unusable."""
    module = _load_module()

    with pytest.raises(RuntimeError) as excinfo:
        module._require_exact_scan_names(
            [_element("Secondary action"), _element("Learn more")],
            "Secondary action",
            label="matched scan",
        )

    assert "['Secondary action', 'Learn more']" in str(excinfo.value)


def test_scan_fail_closed_returns_the_real_error_text():
    module = _load_module()
    error = (
        "no page target matched url_contains='missing-9f2c'; "
        "available URLs: 'http://127.0.0.1:53100/acceptance-pages/target-a.html?probe=9f2c'"
    )

    assert module._require_fail_closed_scan({"error": error, "tool": "browser_scan"}) == error


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"tool": "browser_scan"}, id="silent-success"),
        pytest.param({"error": "boom", "tool": "browser_scan"}, id="wrong-error-text"),
        pytest.param(
            {"error": "no page target matched url_contains='x'", "elements": [_element("Learn more")]},
            id="error-plus-elements",
        ),
        pytest.param(
            {"error": "no page target matched url_contains='x'", "target_url": "http://127.0.0.1:1/"},
            id="error-plus-target-url",
        ),
        pytest.param([_element("Learn more")], id="element-list"),
    ],
)
def test_scan_fail_closed_rejects_silent_success_and_leaked_targets(payload):
    module = _load_module()

    with pytest.raises(RuntimeError):
        module._require_fail_closed_scan(payload)


def test_scan_tab_selection_returns_the_first_of_each_kind():
    module = _load_module()
    tabs = [
        {"url": "http://127.0.0.1:1/acceptance-pages/target-a.html?probe=1", "id": "t1"},
        {"url": "http://127.0.0.1:1/acceptance-pages/decoy.html?probe=1", "id": "d1"},
        {"url": "http://127.0.0.1:1/acceptance-pages/target-a.html?probe=1", "id": "t2"},
        {"url": "http://127.0.0.1:3080/session", "id": "unrelated"},
    ]

    acceptance_tabs, target_tab, decoy_tab = module._select_acceptance_tabs(
        tabs, "target-a.html?probe=1", "decoy.html?probe=1"
    )

    assert len(acceptance_tabs) == 3, "the unrelated dsh tab must not be counted"
    assert target_tab["id"] == "t1"
    assert decoy_tab["id"] == "d1"


@pytest.mark.parametrize(
    ("tabs", "detail"),
    [
        pytest.param(
            [{"url": "http://127.0.0.1:1/acceptance-pages/target-a.html?probe=1", "id": "t1"}],
            "decoys=0",
            id="missing-decoy",
        ),
        pytest.param(
            [{"url": "http://127.0.0.1:1/acceptance-pages/decoy.html?probe=1", "id": "d1"}],
            "targets=0",
            id="missing-target",
        ),
    ],
)
def test_scan_tab_selection_requires_each_side(tabs, detail):
    """Without both tabs the decoy half of the contract was never exercised."""
    module = _load_module()

    with pytest.raises(RuntimeError) as excinfo:
        module._select_acceptance_tabs(tabs, "target-a.html?probe=1", "decoy.html?probe=1")

    assert detail in str(excinfo.value)
    assert "acceptance-pages" not in detail


def test_scan_main_calls_the_contract_checks_it_documents():
    """Wiring only: main() must evaluate the extracted checks, not re-inline them."""
    tree = ast.parse(PROBE.read_text(encoding="utf-8"))
    main_calls = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }

    assert {"_select_acceptance_tabs", "_require_exact_scan_names",
            "_require_fail_closed_scan"} <= main_calls
    assert "_result_content" in main_calls
    assert "openeyes.mcp.server" in "".join(
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    )


def test_scan_acceptance_result_content_parses_single_text_payload():
    module = _load_module()
    result = {"content": [{"type": "text", "text": json.dumps([_element("Learn more")])}]}

    assert module._result_content(result) == [_element("Learn more")]


def test_scan_acceptance_result_content_rejects_multi_part_payloads():
    module = _load_module()
    result = {
        "content": [
            {"type": "text", "text": "[]"},
            {"type": "text", "text": "[]"},
        ]
    }

    with pytest.raises(RuntimeError):
        module._result_content(result)
