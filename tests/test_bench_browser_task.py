"""Pins for the browser task bench that need no browser.

The bench's own claims are only worth what its honesty rules are worth, so the
rules that can be checked without Edge are checked here: a route that cannot
address a task is SKIP (never FAIL, never PASS), a route that can address it is
not skipped, and the repetition maths says what it says.
"""

from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
BENCH = REPO_ROOT / "benchmarks" / "bench_browser_task.py"


def _load():
    spec = importlib.util.spec_from_file_location("bench_browser_task", BENCH)
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolves annotations through sys.modules, so register first.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bench = _load()


class _FakeCtx(bench._Ctx):
    """A context whose CLI calls come from a script, not from a browser."""

    def __init__(self, responses):
        super().__init__(port=9333, token="bench-test", page_url="http://127.0.0.1/x.html")
        self._responses = list(responses)

    def call(self, *args):
        stdout, exit_code, stderr = self._responses.pop(0)
        call = bench.CliCall(
            argv=["python", "-m", "openeyes.cli.main", *args],
            exit_code=exit_code, stdout=stdout, stderr=stderr, elapsed_s=0.0,
        )
        self.calls.append(call)
        return call


def _task(**overrides):
    """A task of the suite's own ``Task`` type - the bench invents no shape."""
    task_type = type(bench._load_suite("core")[0])
    fields = {
        "id": "shadow-toggle",
        "page": "shadow.html",
        "expect_js": "document.title",
        "expected": "on",
        "targets": ("shadow-toggle",),
        "input_text": "",
        "requires": (),
        "note": "",
    }
    fields.update(overrides)
    return task_type(**fields)


def test_verbs_stops_when_scan_cannot_see_the_target():
    """A control the scan cannot see is out of the route's reach: SKIP."""
    ctx = _FakeCtx([("[]", 0, "")])
    outcome = bench._run_verbs(_task(), ctx)
    assert outcome.skip_reason, "a route that cannot address the task must say why"
    assert "cannot address" in outcome.skip_reason
    assert len(ctx.calls) == 1, "no blind --name-contains click after a blind scan"


def test_verbs_is_not_skipped_when_the_scan_sees_the_target():
    elements = [{"name": "shadow-toggle", "control_type": "CheckBox", "state": {}}]
    ctx = _FakeCtx([
        (json.dumps(elements), 0, ""),
        (json.dumps({"ok": True, "clicked": True}), 0, ""),
    ])
    outcome = bench._run_verbs(_task(), ctx)
    assert outcome.skip_reason is None
    assert len(ctx.calls) == 2
    assert [call.argv[3] for call in ctx.calls] == ["browser", "browser"]


def test_percentile_is_nearest_rank_not_interpolated():
    values = [float(n) for n in range(1, 11)]
    assert bench._percentile(values, 90) == 9.0
    assert bench._percentile(values, 50) == 5.0
    assert bench._percentile([7.0], 90) == 7.0
    assert bench._percentile([], 90) == 0.0


def _row(verdict, elapsed, calls=2):
    return {
        "task": "checklist-20",
        "strategy": "verbs",
        "calls": calls,
        "agent_output_bytes": 100,
        "elapsed_s": elapsed,
        "setup_calls": 2,
        "judgement_calls": 1,
        "verdict": verdict,
        "reason": "" if verdict != bench.SKIP else "no verb can address it",
        "detail": {"failed_calls": 0, "route_notes": ["scan"]},
    }


def test_combine_runs_aggregates_p90_and_keeps_a_skip_honest():
    runs = [_row(bench.PASS, 1.0), _row(bench.PASS, 2.0), _row(bench.SKIP, 0.5)]
    row = bench._combine_runs(runs)
    assert row["verdict"] == bench.SKIP, "disagreeing repetitions are not a pass"
    assert row["detail"]["success_rate"] == 1.0
    assert row["detail"]["elapsed_p90_s"] == 2.0
    assert row["detail"]["repeat"] == 3
    assert len(row["detail"]["runs"]) == 3


def test_combine_runs_passes_only_when_every_run_passed():
    row = bench._combine_runs([_row(bench.PASS, 1.0), _row(bench.PASS, 3.0)])
    assert row["verdict"] == bench.PASS
    assert row["detail"]["elapsed_p90_s"] == 3.0
    assert row["elapsed_s"] == 2.0


def test_a_single_run_keeps_the_contract_receipt_shape():
    """--repeat 1 must not grow the receipt the spec describes."""
    row = _row(bench.PASS, 1.5)
    combined = bench._combine_runs([row])
    assert combined is row
    assert "runs" not in combined["detail"]


def test_warmup_runs_are_spent_and_dropped_from_every_metric(monkeypatch):
    """A 30 s cold first run must not reach p90 once --warmup 1 drops it."""
    seen = []

    def fake_measure_once(task, strategy, *, port, fixture_port):
        seen.append((task.id, strategy))
        cold = len(seen) == 1
        return _row(bench.PASS, 30.0 if cold else 1.0), []

    monkeypatch.setattr(bench, "_measure_once", fake_measure_once)
    row, errors = bench._measure(
        _task(), "verbs", port=9333, fixture_port=8000, repeat=3, warmup=1
    )

    assert len(seen) == 4, "one warmup run plus three measured runs"
    assert errors == [], "a warmup run cannot fail the bench"
    assert row["detail"]["repeat"] == 3
    assert row["detail"]["elapsed_p90_s"] == 1.0, "the cold run is not in p90"
    assert row["elapsed_s"] == 1.0
    assert row["calls"] == 2, "the warmup run's calls stay out of calls"
    assert row["detail"]["warmup_discarded"] == 1
    assert row["detail"]["warmup_runs"] == [
        {"verdict": bench.PASS, "elapsed_s": 30.0, "calls": 2, "agent_output_bytes": 100}
    ]


def test_no_warmup_leaves_the_row_untouched_and_parses_off_by_default(monkeypatch):
    assert bench._parse_args([]).warmup == 0
    assert bench._parse_args(["--warmup", "1"]).warmup == 1

    def fake_measure_once(task, strategy, *, port, fixture_port):
        return _row(bench.PASS, 1.5), []

    monkeypatch.setattr(bench, "_measure_once", fake_measure_once)
    row, _ = bench._measure(
        _task(), "verbs", port=9333, fixture_port=8000, repeat=2, warmup=0
    )
    for key in ("warmup", "warmup_discarded", "warmup_runs", "warmup_note"):
        assert key not in row["detail"], f"--warmup 0 must not add {key} to the receipt"


class _FakeEdge:
    """A Popen-shaped route that ignores the polite close, like a debugging Edge."""

    stderr = io.StringIO("")

    def __init__(self, dies_on_kill: bool) -> None:
        self.pid = 4242
        self.dead = False
        self.dies_on_kill = dies_on_kill
        self.calls: list[str] = []

    def poll(self):
        return 0 if self.dead else None

    def terminate(self) -> None:
        self.calls.append("terminate")

    def kill(self) -> None:
        self.calls.append("kill")
        self.dead = self.dies_on_kill

    def wait(self, timeout=None) -> int:
        if self.dead:
            return 0
        raise subprocess.TimeoutExpired("taskkill", timeout or 0.0)


def test_stop_edge_escalates_to_a_force_kill():
    """taskkill without /F does not close a debugging Edge; the run must."""
    edge = _FakeEdge(dies_on_kill=True)
    assert bench._stop_edge(edge) is None
    assert edge.calls == ["terminate", "kill"]


def test_stop_edge_reports_a_survivor_instead_of_raising():
    edge = _FakeEdge(dies_on_kill=False)
    problem = bench._stop_edge(edge)
    assert problem is not None and "still running" in problem


def test_edge_wait_raises_when_the_pid_outlives_the_deadline(monkeypatch):
    """Returning 0 here would suppress the force-kill in ``_stop_process``."""
    monkeypatch.setattr(bench, "_pid_alive", lambda pid: True)
    with pytest.raises(subprocess.TimeoutExpired):
        bench._EdgeProcess(4242).wait(0.05)
