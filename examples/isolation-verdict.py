"""Does this run have to leave the shared desktop? Ask its ledger.

``docs/isolation-policy.md`` (§2) defines three ways a shared desktop stops
being enough: the work needs two agents acting at once, the semantic path is
mostly gone, or the disturbance budget keeps refusing. ``openeyes/agent/
isolation.py`` turns those three numbers into a verdict, but it does not say
where the numbers come from. This tool does: it reads a ledger export, counts
it with ``openeyes.agent.ledger_isolation``, and prints the verdict together
with the evidence it was computed from.

The parser is not copied. ``load_records`` comes from
``examples/interference-report.py``, so a file this tool can read is exactly a
file that report can read, and the refusal rules that decide
``budget_exhausted`` are the ones that report already enforces.

``--window-seconds`` reads the ledger the way the threshold is written down.
``docs/isolation-policy.md`` §2.4 says "3 次/分钟", but a ledger is a whole
run; with ``N > 0`` only refusals whose ``ts`` falls in the last ``N`` seconds
of the ledger are counted (anchored on the newest ``ts`` in the file, not on
the clock, so the same file always gives the same answer). ``entries`` and
therefore ``semantic_miss_rate`` stay over the whole ledger: that threshold is
a share of one run with no time unit, and cutting its denominator on a budget
option would let one switch move two verdicts. The default is ``0`` - the
whole ledger - which is what this tool did before the option existed. The
JSON shows ``window_seconds`` (also as ``windowSeconds``), ``window_start``,
``window_end``, the in-window ``refusals`` and ``refusals_total``, so the
window can be checked instead of trusted.

Two of the four inputs are not in the ledger at all:

* ``--needs-parallel`` - whether the work genuinely requires two agents acting
  at the same moment. The ledger knows two agents *were* awake; it cannot know
  whether them acting together was the requirement (``docs/isolation-policy.md``
  §2.1, §2.4).
* ``--concurrent-writers`` - how many agents want the desktop at once.

Nothing else is guessed. A ledger with no physical input does not become
"parallel", and an empty ledger does not become "healthy": ``0/0`` is printed
as an unmeasured rate and the verdict stays ``none``.

Run it:

    python examples\\isolation-verdict.py --ledger examples\\out\\mouse-cluster-ledger.jsonl
    python examples\\isolation-verdict.py --ledger export.json --json
    python examples\\isolation-verdict.py --ledger export.jsonl --needs-parallel --concurrent-writers 2
    python examples\\isolation-verdict.py --ledger export.jsonl --window-seconds 60

Exit codes: 0 a verdict was produced; 2 the ledger cannot be read, cannot be
counted (a missing ``agent_id``, a flag that is not a boolean, a refusal that
claims a delivery, a ``ts`` that is not a number when a window was asked for),
or the arguments do not make sense.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from openeyes.agent.isolation import (  # noqa: E402
    LEVEL_NONE,
    LEVEL_SESSION,
    LEVEL_VM,
    IsolationConfig,
)
from openeyes.agent.ledger_isolation import (  # noqa: E402
    LedgerIsolationError,
    isolation_report,
)

#: What each level means for the person reading the output.
_LEVEL_LINE = {
    LEVEL_NONE: "stay on the shared desktop",
    LEVEL_SESSION: "move to a separate session: its own foreground window",
    LEVEL_VM: "move to a VM: its own pointers, its own screen",
}


def report_module() -> Any:
    """``examples/interference-report.py``, loaded instead of re-implemented.

    The file name has a hyphen, so it is loaded by path rather than imported
    by name - the same way ``tests/test_interference_report.py`` does it.
    """
    path = Path(__file__).with_name("interference-report.py")
    spec = importlib.util.spec_from_file_location("interference_report", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def render(report: dict, config: IsolationConfig) -> str:
    """The numbers, where they came from, and the verdict they produced."""
    evidence = report["evidence"]
    verdict = report["verdict"]
    entries = evidence["entries"]
    physical = evidence["physical_inputs"]
    rate = evidence["semantic_miss_rate"]

    lines = [f"ledger: {report['ledger'] or '<memory>'}",
             f"evidence: entries={entries} physical_inputs={physical} "
             f"refusals={evidence['refusals']} "
             f"claims_ignored={evidence['claims_ignored']}"]

    if evidence["window_seconds"]:
        # Only when a window was asked for: a default run prints exactly
        # what it printed before the option existed.
        start = evidence["window_start"]
        end = evidence["window_end"]
        span = ("no ts in the ledger" if start is None
                else f"ts {start:.3f} .. {end:.3f}")
        lines.append(
            f"window: last {evidence['window_seconds']:g}s ({span}) "
            f"- {evidence['refusals']} refusals inside, "
            f"{evidence['refusals_total']} in the whole ledger "
            f"(entries are not windowed)")

    if evidence["agents"]:
        lines.append("agents:")
        width = max(len(agent) for agent in evidence["agents"])
        for agent, counts in evidence["agents"].items():
            lines.append(f"  {agent:<{width}}  entries={counts['entries']}  "
                         f"physical_inputs={counts['physical_inputs']}  "
                         f"refusals={counts['refusals']}")

    lines.append(
        f"semantic_miss_rate: {physical}/{entries} = {rate:.4f} "
        + ("" if evidence["measured"] else "(unmeasured: the ledger is empty) ")
        + f"threshold {config.semantic_miss_rate_threshold:.2f}")
    lines.append(f"budget_exhausted: {evidence['budget_exhausted']} refusals "
                 f"threshold {config.budget_exhausted_threshold}")
    lines.append(f"parallel: needs_parallel={evidence['needs_parallel']} "
                 f"concurrent_writers={evidence['concurrent_writers']} "
                 f"threshold concurrent_writers >= {config.parallel_min_agents}")

    level = verdict["level"]
    lines.append(f"verdict: {level} - {_LEVEL_LINE.get(level, level)}")
    for reason in verdict["reasons"]:
        lines.append(f"  {reason}")
    return "\n".join(lines)


def _json_error(ledger: str, problems: list[dict[str, Any]]) -> str:
    return json.dumps({"ok": False, "ledger": ledger, "errors": problems},
                      ensure_ascii=False, indent=2)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ledger", required=True,
                        help="a ledger export: JSONL, or a JSON array")
    parser.add_argument("--needs-parallel", action="store_true",
                        help="the work needs two agents acting at the same "
                             "moment; only the caller knows this")
    parser.add_argument("--concurrent-writers", type=int, default=1,
                        help="how many agents want to act at the same time "
                             "(default 1)")
    parser.add_argument("--window-seconds", type=float, default=0.0,
                        help="count only refusals whose ts falls within this "
                             "many seconds of the ledger's newest ts; 0 (the "
                             "default) counts the whole ledger")
    parser.add_argument("--json", action="store_true",
                        help="print the verdict and its evidence as JSON")
    args = parser.parse_args(argv)
    if args.window_seconds < 0:
        parser.error("--window-seconds may not be negative; 0 means the "
                     "whole ledger")

    try:
        records = report_module().load_records(args.ledger)
    except (OSError, ValueError) as exc:
        problem = f"cannot read the ledger {args.ledger}: {exc}"
        if args.json:
            print(_json_error(args.ledger, [{"problem": str(exc)}]))
        else:
            print(problem)
        return 2

    config = IsolationConfig()
    try:
        report = isolation_report(records, needs_parallel=args.needs_parallel,
                                  concurrent_writers=args.concurrent_writers,
                                  window_seconds=args.window_seconds,
                                  config=config, ledger=args.ledger)
    except LedgerIsolationError as exc:
        if args.json:
            print(_json_error(args.ledger, exc.problems))
        else:
            print(f"{args.ledger} cannot be counted:")
            for problem in exc.problems:
                where = (f"record {problem['record']}"
                         if problem["record"] >= 0 else "record ?")
                field_part = (f" [{problem['field']}]"
                              if problem.get("field") else "")
                print(f"  {where}{field_part}: {problem['problem']}")
        return 2
    except ValueError as exc:
        if args.json:
            print(_json_error(args.ledger, [{"problem": str(exc)}]))
        else:
            print(f"cannot evaluate {args.ledger}: {exc}")
        return 2

    if args.json:
        print(json.dumps({"ok": True, **report}, ensure_ascii=False, indent=2))
    else:
        print(render(report, config))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
