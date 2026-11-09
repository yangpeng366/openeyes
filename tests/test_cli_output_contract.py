"""Contract tests for the CLI output contract.

An agent driving ``eyes`` needs a stable shape to parse and a failure it
can act on. These tests pin both:

* ``--json`` works before *and* after the subcommand - a caller should
  not have to remember which order a given verb accepts;
* the JSON is the same data the human view renders;
* a failure exits non-zero and names the cause.
"""
from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from openeyes.cli.main import cli
from openeyes.cli.output import render_table


def _run(*args):
    """Invoke the CLI with stdout and stderr kept apart.

    An agent pipes stdout into a JSON parser, so the human error line on
    stderr must not pollute stdout - that separation is part of the
    contract, and the tests read ``result.stdout`` for that reason.
    """
    return CliRunner().invoke(cli, list(args), catch_exceptions=False)


# ------------------------------------------------------------- the switch
def test_json_before_the_subcommand():
    result = _run("--json", "windows", "list")
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert "windows" in payload


def test_json_after_the_subcommand():
    result = _run("windows", "list", "--json")
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert "windows" in payload


def test_both_orders_produce_the_same_shape():
    before = json.loads(_run("--json", "windows", "list").output)
    after = json.loads(_run("windows", "list", "--json").output)
    assert sorted(before) == sorted(after)


def test_without_json_the_human_view_is_a_table():
    result = _run("windows", "list")
    assert result.exit_code == 0
    assert "hwnd" in result.output
    with pytest.raises(json.JSONDecodeError):
        json.loads(result.stdout)


# ------------------------------------------------------------- detect
def test_detect_json_reports_the_window_and_the_count():
    result = _run("--json", "detect", "--window", "1")
    # hwnd 1 does not exist, so this must fail loudly rather than pretend
    assert result.exit_code != 0
    assert "error" in result.stdout.lower()


def test_detect_json_on_a_real_window(monkeypatch):
    from openeyes.core.schema import BBox, Center, Element

    element = Element(backend="uia", control_type="Button", name="OK",
                      bbox=BBox(1, 2, 30, 40), center=Center(16, 22),
                      automation_id="OkButton")
    monkeypatch.setattr("openeyes.cli.main.detect_elements",
                        lambda *a, **k: [element])
    result = _run("--json", "detect", "--window", "42")
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["hwnd"] == 42
    assert payload["count"] == 1
    assert payload["elements"][0]["automation_id"] == "OkButton"


def test_detect_honours_the_automation_id_filter(monkeypatch):
    from openeyes.core.schema import BBox, Center, Element

    elements = [
        Element(backend="uia", control_type="Button", name="a",
                bbox=BBox(0, 0, 10, 10), center=Center(5, 5),
                automation_id="First"),
        Element(backend="uia", control_type="Button", name="b",
                bbox=BBox(0, 0, 10, 10), center=Center(5, 5),
                automation_id="Second"),
    ]
    monkeypatch.setattr("openeyes.cli.main.detect_elements",
                        lambda *a, **k: elements)
    result = _run("--json", "detect", "--window", "42",
                  "--automation-id", "Second")
    payload = json.loads(result.stdout)
    assert payload["count"] == 1
    assert payload["elements"][0]["automation_id"] == "Second"


# ------------------------------------------------------------- failures
def test_a_failure_names_the_cause_and_exits_non_zero(monkeypatch):
    monkeypatch.setattr("openeyes.cli.main.detect_elements",
                        lambda *a, **k: (_ for _ in ()).throw(
                            RuntimeError("the window handle is stale")))
    result = _run("--json", "detect", "--window", "999")
    assert result.exit_code == 4
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert "stale" in payload["error"]


def test_a_failure_without_json_says_what_went_wrong_on_stderr(monkeypatch):
    """Without --json the human line goes to stderr, and stdout stays empty.

    An agent that asked for JSON must be able to trust that stdout holds
    JSON and nothing else - so the error line may not leak into it.
    """
    monkeypatch.setattr("openeyes.cli.main.detect_elements",
                        lambda *a, **k: (_ for _ in ()).throw(
                            RuntimeError("the window handle is stale")))
    result = _run("detect", "--window", "999")
    assert result.exit_code == 4
    assert "stale" in result.stderr
    assert result.stdout == ""


def test_click_without_a_target_reports_which_filters_were_used(monkeypatch):
    monkeypatch.setattr("openeyes.cli.main.detect_elements", lambda *a, **k: [])
    result = _run("--json", "click", "--window", "42", "--name-contains", "Save")
    assert result.exit_code == 3
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert "Save" in payload["error"]


def test_a_dry_run_click_never_clicks(monkeypatch):
    from openeyes.core.schema import BBox, Center, Element

    element = Element(backend="uia", control_type="Button", name="Save",
                      bbox=BBox(0, 0, 10, 10), center=Center(5, 5),
                      automation_id="SaveButton")
    monkeypatch.setattr("openeyes.cli.main.detect_elements",
                        lambda *a, **k: [element])

    def fail(*a, **k):
        raise AssertionError("a dry-run must not click")

    monkeypatch.setattr("openeyes.cli.main.click_xy", fail)
    result = _run("--json", "click", "--window", "42", "--name-contains", "Save")
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["clicked"] is False
    assert payload["dry_run"] is True
    assert payload["target"]["automation_id"] == "SaveButton"


# ------------------------------------------------------------- table
def test_render_table_prints_headers_and_rows(capsys):
    render_table(["a", "bb"], [["1", "2"]])
    out = capsys.readouterr().out
    assert "a" in out and "bb" in out
    assert "1" in out and "2" in out


def test_render_table_says_none_when_there_is_nothing(capsys):
    render_table(["a"], [])
    assert "none" in capsys.readouterr().out

# ------------------------------------------------------- the delivery path
def _save_element():
    from openeyes.core.schema import BBox, Center, Element

    return Element(backend="uia", control_type="Button", name="Save",
                   bbox=BBox(0, 0, 10, 10), center=Center(5, 5),
                   automation_id="SaveButton")


def test_a_dry_run_reports_the_delivery_path_it_would_use(monkeypatch):
    monkeypatch.setattr("openeyes.cli.main.detect_elements",
                        lambda *a, **k: [_save_element()])
    result = _run("--json", "click", "--window", "42",
                  "--name-contains", "Save", "--method", "invoke")
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["method"] == "invoke"
    assert payload["physical_input"] is False
    assert payload["focus_stolen"] is False
    assert payload["cursor_moved"] is False


def test_the_default_delivery_path_is_unchanged(monkeypatch):
    """The default stays what it always was: a physical click on the target.

    Zero behaviour change is the rule here - the semantic path is opt-in
    until the delivery ladder lands.
    """
    monkeypatch.setattr("openeyes.cli.main.detect_elements",
                        lambda *a, **k: [_save_element()])
    result = _run("--json", "click", "--window", "42", "--name-contains", "Save")
    payload = json.loads(result.stdout)
    assert payload["method"] == "pixel"
    assert payload["physical_input"] is False


def test_an_invoke_click_leaves_the_pointer_alone(monkeypatch):
    monkeypatch.setattr("openeyes.cli.main.detect_elements",
                        lambda *a, **k: [_save_element()])
    seen = {}

    def fake_click_element(hwnd, **kwargs):
        seen.update(kwargs)
        return {"ok": True, "method": "invoke", "name": "Save",
                "physical_input": False, "cursor_moved": False,
                "focus_stolen": False}

    monkeypatch.setattr("openeyes.core.selector.click_element",
                        fake_click_element)

    def fail(*a, **k):
        raise AssertionError("an invoke must not move the mouse")

    monkeypatch.setattr("openeyes.cli.main.click_xy", fail)

    result = _run("--json", "click", "--window", "42", "--name-contains", "Save",
                  "--method", "invoke", "--go")
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert seen["method"] == "invoke"
    assert payload["method"] == "invoke"
    assert payload["physical_input"] is False
    assert payload["cursor_moved"] is False


def test_an_invoke_never_downgrades_to_a_physical_click(monkeypatch):
    """A control that cannot be invoked is a failure, not a mouse move."""
    monkeypatch.setattr("openeyes.cli.main.detect_elements",
                        lambda *a, **k: [_save_element()])
    monkeypatch.setattr(
        "openeyes.core.selector.click_element",
        lambda *a, **k: {"ok": False, "method": None,
                         "error": "invoke failed: no pattern"})

    def fail(*a, **k):
        raise AssertionError("a failed invoke must not move the mouse")

    monkeypatch.setattr("openeyes.cli.main.click_xy", fail)

    result = _run("--json", "click", "--window", "42", "--name-contains", "Save",
                  "--method", "invoke", "--go")
    assert result.exit_code != 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert "invoke failed" in payload["error"]


def test_an_invoke_needs_a_selector_target():
    """Coordinates are pixels; there is nothing to invoke."""
    result = _run("--json", "click", "--window", "42", "--x", "5", "--y", "6",
                  "--method", "invoke", "--go")
    assert result.exit_code != 0
    assert "invoke" in result.stderr


def test_a_physical_click_says_it_moved_the_pointer(monkeypatch):
    monkeypatch.setattr("openeyes.cli.main.detect_elements",
                        lambda *a, **k: [_save_element()])
    monkeypatch.setattr("openeyes.cli.main.click_xy", lambda *a, **k: None)
    monkeypatch.setattr("openeyes.cli.main.foreground_window", lambda: 42)
    result = _run("--json", "click", "--window", "42",
                  "--name-contains", "Save", "--go")
    payload = json.loads(result.stdout)
    assert payload["method"] == "pixel"
    assert payload["physical_input"] is True
    assert payload["cursor_moved"] is True


def test_a_physical_click_reports_the_focus_it_took(monkeypatch):
    """The focus came onto the target: a cost, and it is reported."""
    monkeypatch.setattr("openeyes.cli.main.detect_elements",
                        lambda *a, **k: [_save_element()])
    monkeypatch.setattr("openeyes.cli.main.click_xy", lambda *a, **k: None)
    reads = iter([7, 42])
    monkeypatch.setattr("openeyes.cli.main.foreground_window",
                        lambda: next(reads))
    result = _run("--json", "click", "--window", "42",
                  "--name-contains", "Save", "--go")
    payload = json.loads(result.stdout)
    assert payload["focus_stolen"] is True
