"""Tests for the ledger -> isolation-verdict mapping.

Plan (``docs/TEST-PLAN.md`` §1):

| Field | Value |
| --- | --- |
| **Behaviour** | The ledger's own numbers - physical-input share and budget refusals - are the evidence ``evaluate_isolation`` reads, with the caller supplying parallelism and an empty ledger never escalating. |
| **Layer** | `unit` - records written by hand, plus the real export in ``examples/out/``. No desktop. |
| **How it is proved** | the evidence against the records; the verdict against the thresholds, including the inclusive edge; the counts against ``examples/interference-report.py`` on the same records. |
| **Risk** | a mapping that invents its own denominator or its own refusal rule would let the verdict disagree with the published report about the same run - two documents, one run, two numbers. |

The two rules the tests bend over backwards to pin: ``0/0`` stays ``none``
(no evidence is not evidence of a clean desktop), and a rate that sits
exactly on the threshold escalates (the policy is inclusive, §2).

One more edge belongs to the policy and not to the mapping: the budget
threshold is written as "3 次/分钟" (§2.4), while a ledger is a whole run.
The window tests pin what ``window_seconds`` does - the anchor is the newest
``ts`` in the file, the start is inclusive, a ``ts`` that cannot be read is
an error rather than a dropped record, and the denominator stays whole - and
what it does *not* do: ``window_seconds=0`` is byte-for-byte the answer this
module gave before the window existed.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from openeyes.agent.isolation import (
    LEVEL_NONE,
    LEVEL_SESSION,
    LEVEL_VM,
    IsolationConfig,
)
from openeyes.agent.ledger import ActionLedger
from openeyes.agent.ledger_isolation import (
    LedgerIsolationError,
    isolation_report,
    isolation_verdict,
    ledger_evidence,
)

REPO = Path(__file__).resolve().parents[1]
REPORT_SCRIPT = REPO / "examples" / "interference-report.py"
VERDICT_SCRIPT = REPO / "examples" / "isolation-verdict.py"
REAL_LEDGER = REPO / "examples" / "out" / "mouse-cluster-ledger.jsonl"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def report_module():
    return _load(REPORT_SCRIPT, "interference_report")


@pytest.fixture(scope="module")
def verdict_script():
    return _load(VERDICT_SCRIPT, "isolation_verdict_example")


def _entry(agent: str = "worker-a", *, physical_input: bool = False,
           focus_stolen: bool = False, verified: bool = True,
           refused: bool = False, ok: bool = True,
           delivery: str = "none", **extra) -> dict:
    """One ledger entry, in the shape the exports write."""
    record = {
        "kind": "entry",
        "agent_id": agent,
        "action": "click",
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


def _refusal(agent: str = "worker-a") -> dict:
    """A refusal written down without an entry of its own."""
    return {"kind": "refusal", "agent_id": agent, "reason": "BUDGET_EXCEEDED"}


def _write_jsonl(path: Path, records: list[dict]) -> Path:
    path.write_text("\n".join(json.dumps(record, ensure_ascii=False)
                              for record in records) + "\n", encoding="utf-8")
    return path


# ------------------------------------------------------------- the empty ledger
def test_an_empty_ledger_stays_on_the_shared_desktop():
    """0/0 is not evidence: the rate is unmeasured and nothing escalates."""
    evidence = ledger_evidence([])
    assert evidence.entries == 0
    assert evidence.physical_inputs == 0
    assert evidence.semantic_miss_rate == 0.0
    assert evidence.measured is False

    verdict = isolation_verdict([])
    assert verdict.escalate is False
    assert verdict.level == LEVEL_NONE
    assert verdict.reasons == []


def test_a_ledger_of_only_claims_measures_nothing():
    """A summary record is a claim about a run, not an action of it."""
    evidence = ledger_evidence([{"kind": "summary", "entries": 40,
                                 "physical_input": 30, "refused": 0,
                                 "verified": 40}])
    assert evidence.entries == 0
    assert evidence.claims_ignored == 1
    assert isolation_verdict([]).level == LEVEL_NONE


# --------------------------------------------------- the physical-input share
def test_a_physical_input_rate_exactly_at_the_threshold_escalates():
    """Half the attempts physical is the inclusive edge: it trips."""
    records = [_entry(physical_input=True, delivery="pixel")] * 2 \
        + [_entry()] * 2
    evidence = ledger_evidence(records)
    assert evidence.entries == 4
    assert evidence.physical_inputs == 2
    assert evidence.semantic_miss_rate == 0.5

    verdict = isolation_verdict(records)
    assert verdict.escalate is True
    assert verdict.level == LEVEL_SESSION
    assert "0.50" in verdict.reasons[0]


def test_a_rate_below_the_threshold_stays_put():
    """Almost half is not half."""
    records = [_entry(physical_input=True, delivery="pixel")] * 2 \
        + [_entry()] * 3
    evidence = ledger_evidence(records)
    assert evidence.semantic_miss_rate < 0.5
    assert isolation_verdict(records).level == LEVEL_NONE


def test_the_denominator_is_every_counted_record():
    """Refusals are attempts too: they stay in the denominator."""
    records = [_entry(physical_input=True, delivery="pixel"),
               _entry(),
               _refusal()]
    evidence = ledger_evidence(records)
    assert evidence.entries == 3
    assert evidence.physical_inputs == 1
    assert evidence.semantic_miss_rate == pytest.approx(1 / 3)


# ------------------------------------------------------------ budget refusals
def test_refusals_reach_the_budget_threshold():
    """Three refusals in the ledger are three refusals in the evidence."""
    records = [_entry() for _ in range(7)] \
        + [_entry(refused=True, ok=False, verified=False) for _ in range(3)]
    evidence = ledger_evidence(records)
    assert evidence.entries == 10
    assert evidence.refusals == 3
    assert evidence.budget_exhausted == evidence.refusals

    verdict = isolation_verdict(records)
    assert verdict.escalate is True
    assert verdict.level == LEVEL_SESSION
    assert "打扰预算耗尽次数" in verdict.reasons[0]
    assert "3 次/分钟" in verdict.reasons[0]


def test_two_refusals_are_not_a_spent_budget():
    """One step below the threshold stays on the desktop."""
    records = [_entry() for _ in range(8)] \
        + [_entry(refused=True, ok=False, verified=False) for _ in range(2)]
    assert ledger_evidence(records).refusals == 2
    assert isolation_verdict(records).level == LEVEL_NONE


def test_a_standalone_refusal_counts_like_a_refused_entry():
    """The two ways a refusal is written down are the same refusal."""
    from_entries = [_entry(refused=True, ok=False, verified=False)] * 3
    from_records = [_refusal() for _ in range(3)]
    assert ledger_evidence(from_entries).refusals == 3
    assert ledger_evidence(from_records).refusals == 3
    assert ledger_evidence(from_records).entries == 3
    assert isolation_verdict(from_records).level == LEVEL_SESSION


# -------------------------------------------------------------- the window
def _refusal_at(ts: float, agent: str = "worker-a") -> dict:
    """A refused entry stamped at ``ts``, in the shape the exports write."""
    return _entry(agent, refused=True, ok=False, verified=False, ts=ts)


def test_a_refusal_outside_the_window_is_not_counted():
    """Last 60s of a ledger whose newest ts is 100.0: [40.0, 100.0]."""
    records = [_entry(ts=10.0),                # fills the ledger, no refusal
               _refusal_at(10.0),              # 90s back: outside
               _refusal_at(90.0),              # 10s back: inside
               _entry(ts=100.0)]               # the anchor
    evidence = ledger_evidence(records, window_seconds=60)
    assert evidence.window_start == 40.0
    assert evidence.window_end == 100.0
    assert evidence.refusals == 1
    assert evidence.refusals_total == 2
    assert evidence.budget_exhausted == 1
    assert isolation_verdict(records, window_seconds=60).level == LEVEL_NONE
    # The same ledger unwindowed counts both: the window is what moved the
    # number, and it moved only that number.
    whole = ledger_evidence(records)
    assert whole.refusals == whole.refusals_total == 2
    assert whole.window_seconds == 0.0 and whole.window_start is None


def test_a_refusal_exactly_at_the_window_start_is_counted():
    """The start is inclusive, like every threshold in the policy."""
    inside = [_refusal_at(40.0), _entry(ts=100.0)]
    outside = [_refusal_at(39.999), _entry(ts=100.0)]
    assert ledger_evidence(inside, window_seconds=60).refusals == 1
    assert ledger_evidence(outside, window_seconds=60).refusals == 0


def test_a_window_can_change_the_verdict_on_its_own():
    """Five refusals in the run, one of them in the last minute."""
    records = [_refusal_at(float(moment))
               for moment in (0.0, 10.0, 20.0, 30.0, 100.0)] \
        + [_entry(ts=100.0)]
    assert isolation_verdict(records).level == LEVEL_SESSION
    windowed = isolation_verdict(records, window_seconds=60)
    assert windowed.level == LEVEL_NONE
    assert isolation_verdict(records, window_seconds=60,
                             config=IsolationConfig(
                                 budget_exhausted_threshold=1)).level \
        == LEVEL_SESSION


def test_the_window_slices_refusals_and_leaves_the_rate_alone():
    """One switch moves one number: ``entries`` and the rate stay whole.

    The denominator is the ledger, not the window, because
    ``semantic_miss_rate`` is a share of a run with no time unit (§2.4).
    The proof is that the windowed and unwindowed evidence agree on every
    field except the refusal counts and the window itself.
    """
    records = [_entry(physical_input=True, delivery="pixel", ts=1.0),
               _entry(ts=2.0),
               _entry(ts=3.0),
               _refusal_at(3.0),
               _refusal_at(50.0)]
    whole = ledger_evidence(records)
    windowed = ledger_evidence(records, window_seconds=10)

    assert windowed.entries == whole.entries == 5
    assert windowed.physical_inputs == whole.physical_inputs == 1
    assert windowed.semantic_miss_rate == whole.semantic_miss_rate
    assert windowed.refusals_total == whole.refusals_total == 2
    assert windowed.refusals == 1
    assert windowed.agents["worker-a"]["entries"] \
        == whole.agents["worker-a"]["entries"]
    assert windowed.agents["worker-a"]["refusals"] == 1
    assert windowed.agents["worker-a"]["refusals_total"] == 2


def test_a_window_only_sees_the_refusals_of_its_own_agents():
    """Per-agent counts are windowed the same way the total is."""
    records = [_refusal_at(10.0, "worker-a"), _refusal_at(90.0, "worker-b"),
               _entry(ts=100.0, agent="worker-b")]
    evidence = ledger_evidence(records, window_seconds=60)
    assert evidence.refusals == 1
    assert evidence.agents["worker-a"]["refusals"] == 0
    assert evidence.agents["worker-a"]["refusals_total"] == 1
    assert evidence.agents["worker-b"]["refusals"] == 1


def test_a_ledger_without_ts_cannot_be_windowed():
    """A record that cannot be placed on the timeline is named, not dropped."""
    missing = _entry(ts=50.0)
    del missing["ts"]
    with pytest.raises(LedgerIsolationError) as excinfo:
        ledger_evidence([_entry(ts=100.0), missing], window_seconds=60)
    problem = excinfo.value.problems[0]
    assert problem["record"] == 1
    assert problem["field"] == "ts"
    assert "ts" in str(excinfo.value)


def test_a_ts_that_is_not_a_number_cannot_be_windowed():
    with pytest.raises(LedgerIsolationError) as excinfo:
        ledger_evidence([_refusal_at(1.0), _entry(ts="2026-11-10T00:00:00")],
                        window_seconds=60)
    assert excinfo.value.problems[0]["field"] == "ts"
    with pytest.raises(LedgerIsolationError):
        ledger_evidence([_entry(ts=float("nan"))], window_seconds=60)
    # Without a window, ts is a label nothing reads, so it is not checked.
    assert ledger_evidence([_entry(ts="later")]).entries == 1


def test_a_negative_window_is_refused():
    with pytest.raises(ValueError):
        ledger_evidence([_entry()], window_seconds=-1)


def test_no_window_is_the_old_answer_byte_for_byte():
    """``window_seconds=0`` and "no option" must be the same bytes.

    Not just the same counts: the same JSON document, so nothing on the
    default path changed when the option was added - apart from the window
    fields, which now report "the whole ledger".
    """
    records = [_entry(physical_input=True, delivery="pixel", ts=1.0),
               _entry(ts=2.0),
               _refusal_at(3.0),
               _refusal_at(4.0),
               _refusal_at(5.0),
               {"kind": "summary", "entries": 5, "physical_input": 1,
                "focus_stolen": 0, "refused": 3, "verified": 2}]
    default = json.dumps(isolation_report(records, ledger="ledger.jsonl"),
                         ensure_ascii=False).encode("utf-8")
    explicit = json.dumps(isolation_report(records, window_seconds=0,
                                           ledger="ledger.jsonl"),
                          ensure_ascii=False).encode("utf-8")
    assert default == explicit

    payload = json.loads(default)
    assert payload["evidence"]["refusals"] == 3
    assert payload["evidence"]["refusals_total"] == 3
    assert payload["evidence"]["window_seconds"] == 0.0
    assert payload["evidence"]["window_start"] is None
    assert payload["verdict"]["level"] == LEVEL_SESSION


def test_the_json_names_the_window_and_both_refusal_counts():
    records = [_refusal_at(10.0), _refusal_at(90.0), _entry(ts=100.0)]
    report = isolation_report(records, window_seconds=60, ledger="l.jsonl")
    evidence = json.loads(json.dumps(report, ensure_ascii=False))["evidence"]
    assert evidence["windowSeconds"] == evidence["window_seconds"] == 60.0
    assert evidence["window_start"] == 40.0
    assert evidence["window_end"] == 100.0
    assert evidence["refusals"] == 1
    assert evidence["refusals_total"] == 2
    assert evidence["budget_exhausted"] == 1


# --------------------------------------------------------------- parallelism
def test_parallel_writers_with_two_agents_need_a_vm():
    """The caller says the work is parallel; the ledger only says what ran."""
    verdict = isolation_verdict([_entry()], needs_parallel=True,
                                concurrent_writers=2)
    assert verdict.escalate is True
    assert verdict.level == LEVEL_VM
    assert "concurrent_writers=2" in verdict.reasons[0]


def test_two_writers_without_parallel_work_stay_put():
    """Two agents that happen to be awake are not parallel work."""
    records = [_entry("worker-a"), _entry("worker-b")]
    verdict = isolation_verdict(records, needs_parallel=False,
                                concurrent_writers=2)
    assert verdict.level == LEVEL_NONE
    assert verdict.escalate is False


def test_parallelism_is_never_read_out_of_the_ledger():
    """A ledger cannot say the work must be parallel, however busy it looks."""
    records = [_entry(physical_input=True, focus_stolen=True,
                      delivery="foreground") for _ in range(9)]
    assert ledger_evidence(records).needs_parallel is False
    assert ledger_evidence(records).concurrent_writers == 1
    assert isolation_verdict(records).level == LEVEL_SESSION


# ---------------------------------------------------------------- the way down
def test_the_same_ledger_comes_back_to_the_shared_desktop():
    """Improve the inputs, and the same evaluation returns ``none``."""
    up = [_entry(physical_input=True, delivery="pixel")] * 2 + [_entry()] * 2
    assert isolation_verdict(up).level == LEVEL_SESSION

    down = [_entry() for _ in range(4)]
    verdict = isolation_verdict(down)
    assert verdict.escalate is False
    assert verdict.level == LEVEL_NONE
    assert verdict.reasons == []

    parallel = isolation_verdict(down, needs_parallel=True,
                                 concurrent_writers=3)
    assert parallel.level == LEVEL_VM
    back = isolation_verdict(down, needs_parallel=False, concurrent_writers=1)
    assert back.level == LEVEL_NONE


def test_a_config_moves_the_threshold_without_touching_the_mapping():
    """The mapping is counts; the thresholds stay in ``evaluate_isolation``."""
    records = [_entry(physical_input=True, delivery="pixel")] + [_entry()] * 2
    assert isolation_verdict(records).level == LEVEL_NONE
    strict = IsolationConfig(semantic_miss_rate_threshold=0.3)
    assert isolation_verdict(records, config=strict).level == LEVEL_SESSION


# --------------------------------------------------- the same rule as the report
def test_the_counts_match_the_interference_report(report_module):
    """One ledger, one set of numbers: the report and the verdict agree."""
    records = [_entry("worker-a"),
               _entry("worker-a", physical_input=True, delivery="pixel"),
               _entry("worker-b", verified=False),
               _entry("worker-b", refused=True, ok=False, verified=False),
               _refusal("worker-b"),
               {"kind": "summary", "entries": 5, "physical_input": 1,
                "focus_stolen": 0, "refused": 2, "verified": 3}]

    report = report_module.summarise(records)
    evidence = ledger_evidence(records)

    assert report.errors == []
    assert evidence.entries == report.totals.entries
    assert evidence.physical_inputs == report.totals.physical_input
    assert evidence.refusals == report.totals.refused
    assert sum(counts["entries"] for counts in evidence.agents.values()) \
        == report.totals.entries
    for agent, counts in evidence.agents.items():
        assert counts["refusals"] == report.agents[agent].refused
        assert counts["physical_inputs"] == report.agents[agent].physical_input


def test_the_report_and_the_verdict_agree_on_the_real_export(report_module):
    """The recorded two-agent run: no physical input, no refusal, no move."""
    if not REAL_LEDGER.exists():
        pytest.skip(f"no recorded ledger at {REAL_LEDGER}")

    records = report_module.load_records(REAL_LEDGER)
    report = report_module.summarise(records, path=str(REAL_LEDGER))
    evidence = ledger_evidence(records)

    assert report.errors == []
    assert evidence.entries == report.totals.entries
    assert evidence.physical_inputs == report.totals.physical_input
    assert evidence.refusals == report.totals.refused
    assert evidence.claims_ignored == report.records["summaries"]
    assert isolation_verdict(records).level == LEVEL_NONE


# --------------------------------------------------- a ledger that cannot count
def test_a_record_without_an_agent_is_refused():
    """Unattributable records would silently shrink the denominator."""
    record = _entry()
    del record["agent_id"]
    with pytest.raises(LedgerIsolationError) as excinfo:
        ledger_evidence([record])
    assert excinfo.value.problems[0]["field"] == "agent_id"


def test_a_flag_that_is_not_a_boolean_is_refused():
    with pytest.raises(LedgerIsolationError):
        ledger_evidence([_entry(physical_input=0)])


def test_a_refusal_that_claims_a_delivery_is_refused():
    """A refusal delivered nothing; a ledger that says otherwise is broken."""
    with pytest.raises(LedgerIsolationError) as excinfo:
        ledger_evidence([_entry(refused=True, ok=False, verified=False,
                                delivery="pixel", physical_input=True)])
    assert excinfo.value.problems[0]["field"] == "refused"


def test_an_unknown_record_kind_is_refused():
    with pytest.raises(LedgerIsolationError):
        ledger_evidence([{"kind": "gossip", "agent_id": "worker-a"}])


# --------------------------------------------------------------- the data shape
def test_the_evidence_is_plain_data_with_its_sources():
    """A reviewer has to be able to read the numbers, not just the verdict."""
    records = [_entry(physical_input=True, delivery="pixel")] * 2 + [_entry()] \
        + [_entry(refused=True, ok=False, verified=False)]
    report = isolation_report(records, ledger="ledger.jsonl")
    payload = json.loads(json.dumps(report, ensure_ascii=False))

    assert payload["ledger"] == "ledger.jsonl"
    assert payload["verdict"]["level"] == LEVEL_SESSION
    evidence = payload["evidence"]
    assert evidence["entries"] == 4
    assert evidence["physical_inputs"] == 2
    assert evidence["refusals"] == 1
    assert evidence["semantic_miss_rate"] == 0.5
    assert evidence["measured"] is True
    assert evidence["mapping"]["semantic_miss_rate"] == "physical_inputs / entries"
    assert evidence["mapping"]["needs_parallel"].startswith("caller")


def test_an_action_ledger_can_be_measured_directly():
    """The live object and the exported records count the same way."""
    ledger = ActionLedger("worker-a")
    ledger.record(action="click", delivery="pixel", physical_input=True,
                  verified=True)
    ledger.record(action="invoke", delivery="semantic", verified=True)

    from_object = ledger_evidence(ledger)
    from_records = ledger_evidence([entry.to_dict() for entry in ledger.entries()])
    assert from_object.to_dict() == from_records.to_dict()
    assert from_object.semantic_miss_rate == 0.5


# ------------------------------------------------------------------- the command
def test_the_command_prints_a_verdict(verdict_script, tmp_path, capsys):
    path = _write_jsonl(tmp_path / "ledger.jsonl",
                        [_entry(), _entry(physical_input=True, delivery="pixel")])
    code = verdict_script.main(["--ledger", str(path)])
    printed = capsys.readouterr().out
    assert code == 0
    assert "semantic_miss_rate: 1/2" in printed
    assert "verdict: session" in printed


def test_the_command_prints_pure_json(verdict_script, tmp_path, capsys):
    path = _write_jsonl(tmp_path / "ledger.jsonl",
                        [_entry(), _entry(refused=True, ok=False,
                                          verified=False)])
    code = verdict_script.main(["--ledger", str(path), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["ok"] is True
    assert payload["verdict"]["level"] == LEVEL_NONE
    assert payload["evidence"]["refusals"] == 1
    assert payload["evidence"]["measured"] is True


def test_the_command_takes_the_parallel_flag(verdict_script, tmp_path, capsys):
    path = _write_jsonl(tmp_path / "ledger.jsonl", [_entry()])
    code = verdict_script.main(["--ledger", str(path), "--json",
                                "--needs-parallel", "--concurrent-writers", "2"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["verdict"]["level"] == LEVEL_VM
    assert payload["evidence"]["concurrent_writers"] == 2


def test_the_command_rejects_an_unreadable_ledger(verdict_script, tmp_path,
                                                  capsys):
    path = tmp_path / "ledger.jsonl"
    path.write_text("not a ledger\n", encoding="utf-8")
    code = verdict_script.main(["--ledger", str(path)])
    assert code == 2
    assert "cannot read" in capsys.readouterr().out


def test_the_command_takes_a_window(verdict_script, tmp_path, capsys):
    """Four refusals in the file, one of them in the last 60 seconds."""
    path = _write_jsonl(tmp_path / "ledger.jsonl",
                        [_refusal_at(0.0), _refusal_at(10.0),
                         _refusal_at(20.0), _refusal_at(100.0),
                         _entry(ts=100.0)])
    code = verdict_script.main(["--ledger", str(path), "--json",
                                "--window-seconds", "60"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["evidence"]["windowSeconds"] == 60.0
    assert payload["evidence"]["refusals"] == 1
    assert payload["evidence"]["refusals_total"] == 4
    assert payload["verdict"]["level"] == LEVEL_NONE


def test_the_command_prints_the_window_in_words(verdict_script, tmp_path, capsys):
    path = _write_jsonl(tmp_path / "ledger.jsonl",
                        [_refusal_at(0.0), _refusal_at(100.0),
                         _entry(ts=100.0)])
    code = verdict_script.main(["--ledger", str(path),
                                "--window-seconds", "60"])
    printed = capsys.readouterr().out
    assert code == 0
    assert "window: last 60s" in printed
    assert "1 refusals inside, 2 in the whole ledger" in printed


def test_the_command_rejects_a_negative_window(verdict_script, tmp_path):
    path = _write_jsonl(tmp_path / "ledger.jsonl", [_entry()])
    with pytest.raises(SystemExit) as excinfo:
        verdict_script.main(["--ledger", str(path), "--window-seconds", "-5"])
    assert excinfo.value.code == 2


def test_the_command_rejects_a_windowed_ledger_without_ts(verdict_script,
                                                          tmp_path, capsys):
    record = _entry()
    del record["ts"]
    path = _write_jsonl(tmp_path / "ledger.jsonl", [record])
    code = verdict_script.main(["--ledger", str(path), "--json",
                                "--window-seconds", "60"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 2
    assert payload["ok"] is False
    assert payload["errors"][0]["field"] == "ts"


def test_the_command_rejects_a_ledger_it_cannot_count(verdict_script, tmp_path,
                                                      capsys):
    path = _write_jsonl(tmp_path / "ledger.jsonl", [{"kind": "entry"}])
    code = verdict_script.main(["--ledger", str(path), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 2
    assert payload["ok"] is False
    assert payload["errors"][0]["field"] == "agent_id"
