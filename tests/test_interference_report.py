"""Tests for the disturbance report, and for its refusal to flatter a run.

Plan (``docs/TEST-PLAN.md`` §1):

| Field | Value |
| --- | --- |
| **Behaviour** | The report answers the four questions of `docs/mouse-cluster.md` §6 step 6 - physical input, focus stolen, refusals, verified - by recounting the ledger, and it refuses a ledger whose stored summary disagrees with its entries. |
| **Layer** | `unit` - files and numbers, no desktop. |
| **How it is proved** | the four numbers against entries written by hand; the exit code and the printed difference when a claim is wrong; the error when an entry cannot be attributed. |
| **Risk** | a report that trusts its input turns a drifted counter into a published claim about a run that did not happen. |

The distinction the tests keep: an inconsistent ledger (exit 2) is not the
same failure as a summary that disagrees with a consistent ledger (exit
1). The first says "this file cannot be read as a ledger"; the second says
"the run's own claim does not describe what it did".
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "examples" / "interference-report.py"


@pytest.fixture(scope="module")
def report_module():
    spec = importlib.util.spec_from_file_location("interference_report", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _entry(agent: str = "worker-a", *, physical_input: bool = False,
           focus_stolen: bool = False, verified: bool = True,
           refused: bool = False, ok: bool = True,
           delivery: str = "none", **extra) -> dict:
    """One ledger entry, as the export writes it."""
    record = {
        "kind": "entry",
        "agent_id": agent,
        "action": "cursor",
        "delivery": delivery,
        "physical_input": physical_input,
        "focus_stolen": focus_stolen,
        "cursor_moved": True,
        "verified": verified,
        "ok": ok,
        "target": "",
        "detail": "",
        "ts": 1.0,
        "refused": refused,
    }
    record.update(extra)
    return record


def _write_jsonl(path: Path, records: list[dict]) -> Path:
    path.write_text("\n".join(json.dumps(record) for record in records) + "\n",
                    encoding="utf-8")
    return path


# ----------------------------------------------------------------- the four numbers
def test_the_four_numbers_are_the_entries(report_module):
    records = [
        _entry("worker-a"),
        _entry("worker-a", verified=False),
        _entry("worker-b", physical_input=True, focus_stolen=True),
        _entry("worker-b", refused=True, ok=False, verified=False),
    ]
    report = report_module.summarise(records)
    assert report.totals.entries == 4
    assert report.totals.physical_input == 1
    assert report.totals.focus_stolen == 1
    assert report.totals.refused == 1
    assert report.totals.verified == 2
    assert report.ok is True


def test_the_ratio_is_verified_over_every_attempt(report_module):
    """An attempt that was refused still happened; it counts in the total."""
    records = [_entry(verified=True)] * 3 + [_entry(verified=False)]
    report = report_module.summarise(records)
    assert report.totals.verified_ratio == pytest.approx(0.75)
    assert report.totals.as_dict()["entries"] == 4


def test_the_ratio_of_an_empty_ledger_is_zero(report_module):
    report = report_module.summarise([])
    assert report.totals.entries == 0
    assert report.totals.verified_ratio == 0.0


def test_the_agents_are_counted_separately_and_add_up(report_module):
    records = [_entry("worker-a")] * 3 + [_entry("worker-b")] * 2
    report = report_module.summarise(records)
    assert report.agents["worker-a"].entries == 3
    assert report.agents["worker-b"].entries == 2
    assert sum(counts.entries for counts in report.agents.values()) == 5
    assert report.totals.entries == 5


def test_a_standalone_refusal_record_counts_once(report_module):
    records = [_entry("worker-a"),
               {"kind": "refusal", "agent_id": "worker-b",
                "reason": "BUDGET_EXCEEDED"}]
    report = report_module.summarise(records)
    assert report.totals.refused == 1
    assert report.totals.entries == 2
    assert report.records["refusals"] == 1


def test_a_refusal_is_not_a_delivery(report_module):
    """A refused action delivered nothing; a file that says otherwise is bad."""
    records = [_entry("worker-b", refused=True, ok=False, verified=False,
                      delivery="pixel", physical_input=True)]
    report = report_module.summarise(records)
    assert report.errors
    assert report.errors[0]["field"] == "refused"
    assert report.ok is False


def test_the_report_reads_jsonl_and_a_json_array(report_module, tmp_path):
    records = [_entry("worker-a"), _entry("worker-b", verified=False)]
    jsonl = _write_jsonl(tmp_path / "ledger.jsonl", records)
    array = tmp_path / "ledger.json"
    array.write_text(json.dumps(records), encoding="utf-8")

    from_jsonl = report_module.summarise(report_module.load_records(jsonl))
    from_array = report_module.summarise(report_module.load_records(array))
    assert from_jsonl.totals.as_dict() == from_array.totals.as_dict()
    assert from_jsonl.totals.entries == 2


def test_a_whole_document_export_is_read_too(report_module, tmp_path):
    document = tmp_path / "export.json"
    document.write_text(json.dumps({
        "entries": [_entry("worker-a")],
        "refusals": [{"agent_id": "worker-b"}],
        "summary": {"entries": 2, "refused": 1, "verified": 1},
    }), encoding="utf-8")
    report = report_module.summarise(report_module.load_records(document))
    assert report.ok is True
    assert report.totals.entries == 2
    assert report.totals.refused == 1


# ------------------------------------------------------------------- bad ledgers
def test_an_entry_without_an_agent_is_an_error(report_module):
    """A shared ledger whose entries cannot be attributed is not usable."""
    record = _entry("worker-a")
    del record["agent_id"]
    report = report_module.summarise([record])
    assert report.errors
    assert report.errors[0]["field"] == "agent_id"
    assert report.totals.entries == 0


def test_a_flag_that_is_not_a_boolean_is_an_error(report_module):
    report = report_module.summarise([_entry(physical_input=0)])
    assert report.errors
    assert report.errors[0]["field"] == "physical_input"


def test_a_missing_flag_is_an_error(report_module):
    record = _entry()
    del record["verified"]
    report = report_module.summarise([record])
    assert report.errors
    assert report.errors[0]["field"] == "verified"


def test_an_unknown_record_kind_is_an_error(report_module):
    report = report_module.summarise([{"kind": "gossip", "agent_id": "a"}])
    assert report.errors
    assert report.errors[0]["field"] == "kind"


def test_a_lying_summary_is_reported_as_a_difference(report_module):
    """The claim is compared with the recount, field by field."""
    records = [
        _entry("worker-a"),
        _entry("worker-b", refused=True, ok=False, verified=False),
        {"kind": "summary", "entries": 2, "refused": 0, "verified": 2,
         "physical_input": 0, "focus_stolen": 0},
    ]
    report = report_module.summarise(records)
    assert report.errors == []
    assert report.diffs
    fields = {diff["field"] for diff in report.diffs}
    assert fields == {"refused", "verified"}
    refused = next(diff for diff in report.diffs if diff["field"] == "refused")
    assert refused == {"scope": "total", "field": "refused",
                       "claimed": 0, "counted": 1}
    assert report.ok is False


def test_a_summary_that_leaves_an_agent_out_is_a_difference(report_module):
    records = [_entry("worker-a"), _entry("worker-b"),
               {"kind": "summary", "agents": {"worker-a": {"entries": 1}}}]
    report = report_module.summarise(records)
    assert any(diff["scope"] == "worker-b" for diff in report.diffs)


def test_two_summaries_are_ambiguous(report_module):
    records = [_entry(), {"kind": "summary", "entries": 1},
               {"kind": "summary", "entries": 1}]
    report = report_module.summarise(records)
    assert report.errors
    assert "more than one summary" in report.errors[0]["problem"]


def test_a_file_that_is_not_json_is_refused(report_module, tmp_path):
    path = tmp_path / "ledger.jsonl"
    path.write_text("this is not a ledger\n", encoding="utf-8")
    with pytest.raises(ValueError):
        report_module.load_records(path)


def test_an_empty_ledger_is_refused(report_module, tmp_path):
    path = tmp_path / "ledger.jsonl"
    path.write_text("\n", encoding="utf-8")
    with pytest.raises(ValueError):
        report_module.load_records(path)


# ------------------------------------------------------------------- the command
def test_a_clean_ledger_exits_zero(report_module, tmp_path, capsys):
    path = _write_jsonl(tmp_path / "ledger.jsonl",
                        [_entry("worker-a"), _entry("worker-b")])
    code = report_module.main(["--ledger", str(path)])
    printed = capsys.readouterr().out
    assert code == 0
    for name in ("physical_input", "focus_stolen", "refused", "verified"):
        assert name in printed
    assert "verified/total: 2/2" in printed


def test_a_bad_ledger_exits_two(report_module, tmp_path, capsys):
    path = _write_jsonl(tmp_path / "ledger.jsonl", [{"kind": "entry"}])
    code = report_module.main(["--ledger", str(path)])
    printed = capsys.readouterr().out
    assert code == 2
    assert "agent_id" in printed


def test_a_missing_file_exits_two(report_module, tmp_path, capsys):
    code = report_module.main(["--ledger", str(tmp_path / "nope.jsonl")])
    printed = capsys.readouterr().out
    assert code == 2
    assert "cannot read" in printed


def test_a_lying_summary_exits_one_and_prints_the_difference(
        report_module, tmp_path, capsys):
    path = _write_jsonl(tmp_path / "ledger.jsonl",
                        [_entry(), {"kind": "summary", "verified": 99}])
    code = report_module.main(["--ledger", str(path)])
    printed = capsys.readouterr().out
    assert code == 1
    assert "CLAIM MISMATCH" in printed
    assert "claimed=99" in printed
    assert "counted=1" in printed


def test_the_json_output_carries_the_same_numbers(report_module, tmp_path, capsys):
    path = _write_jsonl(tmp_path / "ledger.jsonl",
                        [_entry("worker-a"),
                         _entry("worker-b", physical_input=True)])
    code = report_module.main(["--ledger", str(path), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["ok"] is True
    assert payload["totals"]["physical_input"] == 1
    assert payload["totals"]["verified_ratio"] == 1.0
    assert payload["agents"]["worker-a"]["entries"] == 1


def test_the_json_output_reports_a_mismatch_too(report_module, tmp_path, capsys):
    path = _write_jsonl(tmp_path / "ledger.jsonl",
                        [_entry(), {"kind": "summary", "entries": 5}])
    code = report_module.main(["--ledger", str(path), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 1
    assert payload["diffs"] == [{"scope": "total", "field": "entries",
                                 "claimed": 5, "counted": 1}]
