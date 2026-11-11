"""Behavioural tests for the direct MCP stdio browser_click acceptance probe.

The previous revision asserted that sentences such as "matched call was not a
dry-run" existed in the probe source. Any regression that left the sentence in
place - `clicked` flipping to True, the selector resolving the decoy element,
`center` becoming a constant - still printed ``passed: true``. These tests call
the probe's contract functions with realistic MCP payloads and assert the
values that decide the verdict.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "examples" / "browser-click-acceptance.py"
RUNBOOK = ROOT / "docs" / "dsh-web-acceptance.md"


def _load_module():
    spec = importlib.util.spec_from_file_location("browser_click_acceptance", PROBE)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _target(name: str = "Learn more", *, x: int = 120, y: int = 240) -> dict:
    """An element exactly as ``browser_click`` serialises it in its payload."""
    return {
        "backend": "cdp",
        "control_type": "Hyperlink",
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


def _dry_run_payload(**overrides) -> dict:
    target = _target()
    payload = {
        "clicked": False,
        "would_click": True,
        "center": dict(target["center"]),
        "target": target,
    }
    payload.update(overrides)
    return payload


def test_click_contract_accepts_a_real_dry_run_payload():
    module = _load_module()

    accepted = module._require_dry_run_click(_dry_run_payload(), "Learn more")

    assert accepted["would_click"] is True
    assert accepted["target"]["center"] == {"x": 168, "y": 250}


@pytest.mark.parametrize(
    ("overrides", "detail"),
    [
        pytest.param({"clicked": True, "called": True}, "not a dry-run", id="live-click"),
        pytest.param({"would_click": False}, "not a dry-run", id="nothing-resolved"),
        pytest.param({"would_click": 1}, "not a dry-run", id="truthy-not-true"),
        pytest.param({"target": _target("Secondary action")}, "wrong element", id="decoy-element"),
        pytest.param(
            {"center": {"x": 10, "y": 10}},
            "centre that is not the target's",
            id="placeholder-centre",
        ),
        pytest.param({"target": {}}, "wrong element", id="missing-target-dict"),
    ],
)
def test_click_contract_rejects_anything_but_the_resolved_dry_run(overrides, detail):
    """Each case is a regression that used to print passed=true."""
    module = _load_module()

    with pytest.raises(RuntimeError) as excinfo:
        module._require_dry_run_click(_dry_run_payload(**overrides), "Learn more")

    assert detail in str(excinfo.value)


def test_click_contract_names_the_element_it_resolved():
    """A failing run must show which element was picked, not just that it failed."""
    module = _load_module()

    with pytest.raises(RuntimeError) as excinfo:
        module._require_dry_run_click(_dry_run_payload(target=_target("Secondary action")), "Learn more")

    assert "'Secondary action'" in str(excinfo.value)


def test_click_fail_closed_returns_the_real_error_text():
    module = _load_module()
    error = (
        "no page target matched url_contains='missing-target'; "
        "available URLs: 'http://127.0.0.1:53100/acceptance-pages/target-a.html'"
    )

    assert module._require_fail_closed_click({"error": error, "tool": "browser_click"}) == error


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"tool": "browser_click"}, id="silent-success"),
        pytest.param({"error": "no element matched the selector"}, id="wrong-error-text"),
        pytest.param(
            {"error": "no page target matched url_contains='x'", "would_click": True},
            id="proposed-click",
        ),
        pytest.param(
            {"error": "no page target matched url_contains='x'", "clicked": False},
            id="reported-click-outcome",
        ),
        pytest.param(
            {"error": "no page target matched url_contains='x'", "target": _target()},
            id="leaked-target",
        ),
        pytest.param([_target()], id="element-list"),
    ],
)
def test_click_fail_closed_rejects_silent_success_and_proposed_actions(payload):
    module = _load_module()

    with pytest.raises(RuntimeError):
        module._require_fail_closed_click(payload)


def test_click_main_calls_the_contract_checks_it_documents():
    """Wiring only: main() must evaluate the extracted checks, not re-inline them."""
    tree = ast.parse(PROBE.read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]
    main_calls = {node.func.id for node in calls}
    strings = "".join(
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    )
    url_filters = {
        keyword.value.id
        for node in calls
        if node.func.id == "_click_arguments"
        for keyword in node.keywords
        if keyword.arg == "url_contains" and isinstance(keyword.value, ast.Name)
    }
    go_arguments = [
        node
        for node in calls
        if node.func.id == "_click_arguments"
        and any(keyword.arg == "go" for keyword in node.keywords)
    ]

    assert {
        "_click_arguments",
        "_require_dry_run_click",
        "_require_hittable",
        "_hit_test",
        "_require_fail_closed_click",
        "_require_fail_closed_click_miss",
        "_result_content",
    } <= main_calls
    assert "openeyes.mcp.server" in strings
    assert "notifications/initialized" in strings
    assert url_filters == {"click_filter", "MISSING_FILTER"}, url_filters
    assert go_arguments == [], "the probe must never ask for a live click"
    assert "3080" not in strings, "the direct probe must not depend on the dsh web host"


def test_acceptance_result_content_parses_single_text_payload():
    module = _load_module()
    result = {"content": [{"type": "text", "text": json.dumps({"would_click": True})}]}

    assert module._result_content(result) == {"would_click": True}


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


def test_a_covered_centre_fails_the_hittable_check():
    """An overlay on the reported centre is the failure this check exists for."""
    module = _load_module()
    overlay_report = {"matched": False, "hit": "div#cover", "wanted": "#learn-more"}

    with pytest.raises(RuntimeError, match="covered by"):
        module._require_hittable(
            overlay_report, center={"x": 10, "y": 12}, selector="#learn-more"
        )

    assert module._require_hittable(
        {"matched": True, "hit": "a#learn-more"}, center={"x": 10, "y": 12}, selector="#learn-more"
    )["matched"] is True


def test_the_hittable_check_needs_a_real_measurement():
    module = _load_module()

    with pytest.raises(RuntimeError, match="no measurement"):
        module._require_hittable(None, center={"x": 1, "y": 2}, selector="#learn-more")


def test_an_absent_name_that_leaks_a_proposal_is_not_fail_closed():
    module = _load_module()
    leaked = {
        "error": module.NO_ELEMENT_ERROR,
        "would_click": True,
        "target": {"name": "Learn more"},
    }

    with pytest.raises(RuntimeError, match="proposed"):
        module._require_fail_closed_click_miss(leaked, name="Absent")

    assert module._require_fail_closed_click_miss(
        {"error": module.NO_ELEMENT_ERROR}, name="Absent"
    ) == module.NO_ELEMENT_ERROR


def test_a_dry_run_that_reports_a_click_is_rejected():
    module = _load_module()
    payload = {
        "clicked": True,
        "would_click": True,
        "target": {"name": "Learn more", "center": {"x": 5, "y": 6}, "bbox": {"x": 0, "y": 0, "w": 9, "h": 9}},
        "center": {"x": 5, "y": 6},
    }

    with pytest.raises(RuntimeError, match="not a dry-run"):
        module._require_dry_run_click(payload, expected_name="Learn more")


def test_a_dry_run_aiming_outside_the_box_is_rejected():
    module = _load_module()
    payload = {
        "clicked": False,
        "would_click": True,
        "target": {
            "name": "Learn more",
            "center": {"x": 5, "y": 6},
            "bbox": {"x": 0, "y": 0, "w": 9, "h": 9},
        },
        "center": {"x": 5, "y": 6},
    }
    payload["center"] = {"x": 5, "y": 60}

    with pytest.raises(RuntimeError, match="centre that is not the target"):
        module._require_dry_run_click(payload, expected_name="Learn more")