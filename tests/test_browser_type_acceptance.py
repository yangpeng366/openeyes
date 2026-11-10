"""Behavioural tests for the direct MCP stdio browser_type acceptance probe.

The previous revision read the probe as text and asserted that sentences such as
"matched call was not a dry-run" were present. A probe that sent the wrong
characters, resolved the decoy element, reported a character count that merely
matched the string length, or scored an error payload as a resolved target still
printed ``passed: true`` and stayed green. These tests call the probe's own
contract functions with the payloads the MCP server really serialises and assert
the values that decide the verdict.
"""
from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "examples" / "browser-type-acceptance.py"
TARGET_URL = "http://127.0.0.1:53100/acceptance-pages/target-a.html?probe=9f2c"
DECOY_URL = "http://127.0.0.1:53100/acceptance-pages/decoy.html?probe=9f2c"
TARGET_FILTER = "target-a.html?probe=9f2c"
TEXT = "probe"


def _load_module():
    spec = importlib.util.spec_from_file_location("browser_type_acceptance", PROBE)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _target(name: str = "Learn more", *, x: int = 120, y: int = 240) -> dict:
    """An element exactly as ``browser_type`` serialises ``Element.to_dict()``."""
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


def _matched_payload(**overrides) -> dict:
    payload = {
        "sent": False,
        "sent_chars": 0,
        "would_send_chars": len(TEXT),
        "into": "Learn more",
        "press_enter": False,
        "dry_run": True,
        "target": _target(),
        "target_url": TARGET_URL,
    }
    payload.update(overrides)
    return payload


def _focused_payload(**overrides) -> dict:
    payload = {
        "sent": False,
        "sent_chars": 0,
        "would_send_chars": len(TEXT),
        "into": "(focused)",
        "press_enter": False,
        "dry_run": True,
        "target": None,
        "target_url": TARGET_URL,
    }
    payload.update(overrides)
    return payload


def _check_matched(payload: dict) -> dict:
    module = _load_module()
    return module._require_type_dry_run(
        payload,
        text=TEXT,
        into="Learn more",
        target_name="Learn more",
        target_url=TARGET_URL,
    )


def test_type_contract_accepts_the_real_matched_dry_run():
    accepted = _check_matched(_matched_payload())

    assert accepted["would_send_chars"] == 5
    assert accepted["target"]["name"] == "Learn more"
    assert accepted["target_url"] == TARGET_URL
    assert accepted["dry_run"] is True


@pytest.mark.parametrize(
    ("payload", "detail"),
    [
        pytest.param(
            {"sent": True, "sent_chars": 5, "dry_run": False},
            "not a dry-run",
            id="live-type",
        ),
        pytest.param({"dry_run": False}, "not a dry-run", id="forced-run"),
        pytest.param({"sent": None}, "not a dry-run", id="missing-sent-flag"),
        pytest.param({"sent_chars": 5}, "inserted characters", id="inserted-despite-dry-run"),
        pytest.param({"would_send_chars": 4}, "readback mismatch", id="truncated-readback"),
        pytest.param({"would_send_chars": 6}, "readback mismatch", id="padded-readback"),
        pytest.param({"would_send_chars": None}, "readback mismatch", id="absent-readback"),
        pytest.param({"into": "(focused)"}, "wrong element", id="into-focused"),
        pytest.param({"target": _target("Secondary action")}, "wrong element", id="decoy-element"),
        pytest.param({"target": {}}, "wrong element", id="empty-target"),
        pytest.param({"target": None}, "wrong element", id="unresolved-target"),
        pytest.param({"press_enter": True}, "Enter key", id="proposed-submit"),
        pytest.param({"press_enter": None}, "Enter key", id="absent-press-enter"),
        pytest.param({"target_url": DECOY_URL}, "wrong target URL", id="decoy-url"),
        pytest.param(
            {"error": "no element matched the selector", "sent": False},
            "failed instead of resolving a target",
            id="error-scored-as-success",
        ),
        pytest.param(
            [{"name": "Learn more"}], "non-object payload", id="element-list"
        ),
    ],
)
def test_type_contract_rejects_anything_but_the_resolved_dry_run(payload, detail):
    """Every case is a regression that used to print passed=true."""
    payload = payload if isinstance(payload, list) else _matched_payload(**payload)

    with pytest.raises(RuntimeError) as excinfo:
        _check_matched(payload)

    assert detail in str(excinfo.value)


def test_type_contract_names_the_element_it_resolved():
    with pytest.raises(RuntimeError) as excinfo:
        _check_matched(_matched_payload(target=_target("Secondary action")))

    assert "'Secondary action'" in str(excinfo.value)


def test_type_contract_sees_the_selectorless_dry_run():
    module = _load_module()

    accepted = module._require_type_dry_run(
        _focused_payload(), text=TEXT, into="(focused)", target_url=TARGET_URL
    )

    assert accepted["into"] == "(focused)"
    assert accepted["target"] is None


@pytest.mark.parametrize(
    ("overrides", "detail"),
    [
        pytest.param({"target": _target()}, "resolved an element", id="resolved-a-target"),
        pytest.param({"into": "Learn more"}, "wrong element", id="into-named"),
        pytest.param({"would_send_chars": 3}, "readback mismatch", id="short-readback"),
        pytest.param({"target_url": TARGET_URL + "&x=1"}, "wrong target URL", id="wrong-url"),
    ],
)
def test_type_contract_rejects_a_selectorless_call_that_resolved_something(overrides, detail):
    module = _load_module()

    with pytest.raises(RuntimeError) as excinfo:
        module._require_type_dry_run(
            _focused_payload(**overrides),
            text=TEXT,
            into="(focused)",
            target_url=TARGET_URL,
        )

    assert detail in str(excinfo.value)


def test_type_text_must_be_verbatim():
    module = _load_module()

    assert module._require_exact_text({"text": TEXT}, TEXT) == "probe"
    assert module._require_exact_text({"text": "40 字节中文"}, "40 字节中文") == "40 字节中文"


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("probe ", id="trailing-space"),
        pytest.param("probe\n", id="trailing-newline"),
        pytest.param(" probe", id="leading-space"),
        pytest.param("Probe", id="case-change"),
        pytest.param("prob", id="truncated"),
        pytest.param("probee", id="duplicated-tail"),
        pytest.param("", id="empty"),
        pytest.param(5, id="integer"),
        pytest.param(None, id="missing"),
    ],
)
def test_type_text_rejects_any_drift_from_the_expected_characters(text):
    """Same length, different characters - the old count-only check passed them."""
    module = _load_module()

    with pytest.raises(RuntimeError) as excinfo:
        module._require_exact_text({"text": text}, TEXT)

    message = str(excinfo.value)
    assert "not verbatim" in message or "non-string" in message


def test_type_request_matches_the_wire_contract():
    module = _load_module()
    arguments = module._type_arguments(
        9222, text=TEXT, url_contains=TARGET_FILTER, name_contains="Learn more"
    )

    assert arguments == {
        "port": 9222,
        "url_contains": TARGET_FILTER,
        "name_contains": "Learn more",
        "text": "probe",
    }
    assert "dry_run" not in arguments
    assert (
        module._require_type_request(
            arguments,
            url_contains=TARGET_FILTER,
            name_contains="Learn more",
            expected_text=TEXT,
        )
        is arguments
    )


def test_type_request_omits_every_selector_when_none_is_asked_for():
    module = _load_module()
    arguments = module._type_arguments(9222, text=TEXT, url_contains=TARGET_FILTER)

    assert arguments == {"port": 9222, "url_contains": TARGET_FILTER, "text": "probe"}
    module._require_type_request(
        arguments, url_contains=TARGET_FILTER, name_contains=None, expected_text=TEXT
    )


@pytest.mark.parametrize(
    ("arguments", "kwargs", "detail"),
    [
        pytest.param(
            {"port": 9222, "url_contains": "other", "text": TEXT},
            {"url_contains": TARGET_FILTER, "name_contains": None, "expected_text": TEXT},
            "scoped the wrong tab",
            id="wrong-url-filter",
        ),
        pytest.param(
            {"port": 9222, "url_contains": TARGET_FILTER, "name_contains": "learn more", "text": TEXT},
            {"url_contains": TARGET_FILTER, "name_contains": "Learn more", "expected_text": TEXT},
            "wrong selector",
            id="case-changed-selector",
        ),
        pytest.param(
            {"port": 9222, "url_contains": TARGET_FILTER, "hint": "J", "text": TEXT},
            {"url_contains": TARGET_FILTER, "name_contains": None, "expected_text": TEXT},
            "unexpected selector",
            id="hint-on-selectorless",
        ),
        pytest.param(
            {"port": 9222, "url_contains": TARGET_FILTER, "text": TEXT, "press_enter": True},
            {"url_contains": TARGET_FILTER, "name_contains": None, "expected_text": TEXT},
            "Enter key",
            id="submit-variant",
        ),
        pytest.param(
            {"port": 9222, "url_contains": TARGET_FILTER, "text": TEXT, "dry_run": False},
            {"url_contains": TARGET_FILTER, "name_contains": None, "expected_text": TEXT},
            "dry-run default",
            id="forced-execution",
        ),
        pytest.param(
            {"port": 9222, "url_contains": TARGET_FILTER, "text": "probe "},
            {"url_contains": TARGET_FILTER, "name_contains": None, "expected_text": TEXT},
            "not verbatim",
            id="text-drift",
        ),
    ],
)
def test_type_request_rejects_a_request_the_probe_does_not_mean(arguments, kwargs, detail):
    module = _load_module()

    with pytest.raises(RuntimeError) as excinfo:
        module._require_type_request(arguments, **kwargs)

    assert detail in str(excinfo.value)


def test_type_fail_closed_returns_the_real_error_text():
    module = _load_module()
    error = (
        "no page target matched url_contains='missing-9f2c'; "
        "available URLs: 'http://127.0.0.1:53100/acceptance-pages/target-a.html?probe=9f2c'"
    )

    assert (
        module._require_fail_closed_type(
            {"error": error, "tool": "browser_type"}, expected=module.NO_PAGE_ERROR
        )
        == error
    )
    assert module.NO_PAGE_ERROR in error


def test_type_fail_closed_names_a_missing_element_too():
    module = _load_module()
    payload = {"error": "no element matched the selector", "sent": False, "tool": "browser_type"}

    assert (
        module._require_fail_closed_type(payload, expected=module.NO_ELEMENT_ERROR)
        == "no element matched the selector"
    )


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        pytest.param({"tool": "browser_type"}, "no page target matched url_contains", id="silent-success"),
        pytest.param({"error": "boom"}, "no page target matched url_contains", id="wrong-error-text"),
        pytest.param(
            {"error": "no page target matched url_contains='x'", "would_send_chars": 5},
            "no page target matched url_contains",
            id="proposed-text",
        ),
        pytest.param(
            {"error": "no page target matched url_contains='x'", "target": _target()},
            "no page target matched url_contains",
            id="leaked-target",
        ),
        pytest.param(
            {"error": "no page target matched url_contains='x'", "target_url": TARGET_URL},
            "no page target matched url_contains",
            id="leaked-target-url",
        ),
        pytest.param(
            {
                "error": "no page target matched url_contains='x'",
                "sent": True,
                "sent_chars": 5,
            },
            "no page target matched url_contains",
            id="reported-send",
        ),
        pytest.param(
            {"error": "no element matched the selector", "would_send_chars": 5},
            "no element matched the selector",
            id="element-miss-still-proposed-text",
        ),
        pytest.param([_target()], "no page target matched url_contains", id="element-list"),
    ],
)
def test_type_fail_closed_rejects_silent_success_and_proposed_actions(payload, expected):
    """Neither refusal branch may be accepted as the other, or as a success."""
    module = _load_module()

    with pytest.raises(RuntimeError):
        module._require_fail_closed_type(payload, expected=expected)


def test_type_tab_selection_returns_the_first_of_each_kind():
    module = _load_module()
    tabs = [
        {"url": TARGET_URL, "id": "t1"},
        {"url": DECOY_URL, "id": "d1"},
        {"url": TARGET_URL, "id": "t2"},
        {"url": "http://127.0.0.1:3080/session", "id": "unrelated"},
    ]

    acceptance_tabs, target_tab, decoy_tab = module._select_acceptance_tabs(
        tabs, TARGET_FILTER
    )

    assert len(acceptance_tabs) == 3, "the unrelated dsh tab must not be counted"
    assert target_tab["id"] == "t1"
    assert decoy_tab["id"] == "d1"


@pytest.mark.parametrize(
    ("tabs", "detail"),
    [
        pytest.param([{"url": TARGET_URL, "id": "t1"}], "decoys=0", id="missing-decoy"),
        pytest.param([{"url": DECOY_URL, "id": "d1"}], "targets=0", id="missing-target"),
        pytest.param([], "targets=0", id="no-tabs"),
    ],
)
def test_type_tab_selection_requires_each_side(tabs, detail):
    """Without both tabs the decoy half of the contract was never exercised."""
    module = _load_module()

    with pytest.raises(RuntimeError) as excinfo:
        module._select_acceptance_tabs(tabs, TARGET_FILTER)

    assert detail in str(excinfo.value)


def test_type_main_calls_the_contract_checks_it_documents():
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
        if node.func.id in {"_type_arguments", "_require_type_request"}
        for keyword in node.keywords
        if keyword.arg == "url_contains" and isinstance(keyword.value, ast.Name)
    }
    press_enter = [
        node
        for node in calls
        if node.func.id == "_type_arguments"
        and any(keyword.arg == "press_enter" for keyword in node.keywords)
    ]

    assert {
        "_select_acceptance_tabs",
        "_require_type_request",
        "_require_type_dry_run",
        "_require_fail_closed_type",
        "_result_content",
    } <= main_calls
    assert "openeyes.mcp.server" in strings
    assert "notifications/initialized" in strings
    assert url_filters == {"target_filter", "missing_filter"}, url_filters
    assert press_enter == [], "the probe must never dispatch the Enter key"
    assert "3080" not in strings, "the direct probe must not depend on the dsh web host"


def test_type_acceptance_result_content_parses_single_text_payload():
    module = _load_module()
    result = {"content": [{"type": "text", "text": json.dumps(_matched_payload())}]}

    assert module._result_content(result) == _matched_payload()


def test_type_acceptance_result_content_rejects_multi_part_payloads():
    module = _load_module()
    result = {
        "content": [
            {"type": "text", "text": "{}"},
            {"type": "text", "text": "{}"},
        ]
    }

    with pytest.raises(RuntimeError):
        module._result_content(result)
