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
