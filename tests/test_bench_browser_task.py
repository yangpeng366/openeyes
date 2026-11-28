"""Pins for the browser task bench that need no browser.

The bench's own claims are only worth what its honesty rules are worth, so the
rules that can be checked without Edge are checked here: a route that cannot
address a task is SKIP (never FAIL, never PASS), a route that can address it is
not skipped, and the repetition maths says what it says.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

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
