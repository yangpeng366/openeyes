"""The action ledger, measured the way the isolation verdict wants it.

``evaluate_isolation`` (``openeyes/agent/isolation.py``, semantics in
``docs/isolation-policy.md``) decides whether this work has to leave the
desktop the human is using. It eats three numbers and does not care where
they come from. This module is where two of them come from: the
``ActionLedger``, or an exported ledger read back as plain records. It
counts, it does not judge - the thresholds stay in ``evaluate_isolation``,
so there is exactly one place where a number becomes a verdict.

The mapping, one row per parameter of ``evaluate_isolation``:

====================================  ===========================================
``evaluate_isolation`` parameter       taken from
====================================  ===========================================
``semantic_miss_rate``                 ``physical_inputs / entries`` - the share of
                                       counted actions that drove the user's pointer
``budget_exhausted``                   ``refusals`` - actions an attention or focus
                                       budget refused
``needs_parallel``                     the caller, never the ledger
``concurrent_writers``                 the caller, never the ledger
====================================  ===========================================

Where each number comes from, and where it stops being true:

* **``entries``** - the denominator, and the only one this module uses.
  Every counted record is one attempt: an ``entry`` record, or a
  standalone ``refusal`` record. A ``summary`` record is a claim about
  the run, not an action, so it is ignored (and counted in
  ``claims_ignored`` so a reviewer can see it was seen and skipped).
  *Limit*: the ledger has no notion of "one attempt" beyond what the
  recorder wrote, so a demo that logs one entry per drawn frame weighs
  the same as a run that logs one entry per delivery. The rate is only
  as meaningful as the recording granularity.

* **``physical_inputs``** - records whose ``physical_input`` flag is true.
  That flag already means "this action drove the user's pointer"; a user
  moving their own mouse never sets it (``ActionLedger`` only writes it
  from a delivered pixel/foreground action). Refused records deliver
  nothing, so they can never contribute.

* **``semantic_miss_rate`` = ``physical_inputs / entries``** - the
  physical-input rate, read as "the share of attempts that could not get
  where they were going without the pointer". *Limit*: physical input is
  not the same thing as a failed semantic path. It also covers a
  deliberate pixel click on a control that had a working UIA pattern,
  and it cannot see a semantic attempt that failed *before* anything was
  delivered. It is a lower bound on semantic failure, not an exact
  measurement of it - which is why the default threshold sits at half,
  not at a hair above zero.

* **``refusals`` / ``budget_exhausted``** - a record is a refusal when it
  carries ``refused: true``, or when it is a standalone ``refusal``
  record. The rules are the ones ``examples/interference-report.py``
  already enforces, deliberately not a second set: a refusal must carry
  ``delivery == "none"``, ``ok == false`` and ``verified == false``
  (nothing was delivered, nothing succeeded, nothing was observed), and a
  ledger that says otherwise is inconsistent - this module raises rather
  than counting it. *Limit*: the ledger has no notion of a window of its
  own, so the caller has to name one (``window_seconds``, below).

  *Window*: the threshold in ``IsolationConfig`` is documented as "3 per
  minute" (``docs/isolation-policy.md`` §2.4), while the ledger is a
  whole run. ``window_seconds=N`` supplies the window: a refusal is
  counted only when its ``ts`` lies in ``[latest_ts - N, latest_ts]``,
  inclusive at the start like every threshold in ``evaluate_isolation``,
  where ``latest_ts`` is the newest ``ts`` in the ledger handed in - the
  anchor is the data, never the wall clock, so the same file measured
  tomorrow gives the same answer. ``window_seconds=0`` keeps the old
  behaviour: the whole ledger.

  Only the refusals are sliced, never ``entries``. ``semantic_miss_rate``
  is documented as "语义失败次数 ÷ 总尝试次数" - a share of one run, with
  no time unit (``docs/isolation-policy.md`` §2.4) - so cutting its
  denominator on a *budget* option would move two verdicts with one
  switch, and a quiet minute would inflate the rate by shrinking the
  denominator instead of by any change in the work. The two numbers have
  different units (a count per window, a share per ledger), and each is
  compared against the threshold written for it. To keep the window from
  hiding what it dropped, the evidence reports ``refusals`` (in the
  window) *and* ``refusals_total`` (the whole ledger).

  A window needs every counted record's ``ts`` to be a finite number: the
  anchor is only unambiguous if every record can be placed on the same
  timeline, so a ``ts`` that is missing or not a number is reported by
  record index and field instead of being dropped. Without a window,
  ``ts`` is a label nobody reads and is not validated.

* **``needs_parallel`` / ``concurrent_writers``** - never derived here.
  The ledger cannot tell "two agents are awake" from "two agents must act
  at the same time"; only the caller knows which work it is running. A
  ledger full of ``focus_stolen`` entries is evidence of disturbance, not
  of parallelism, and ``docs/isolation-policy.md`` §2.1 says so.

One more rule, and it is the one that keeps a quiet run quiet: an empty
ledger is ``0/0``. The rate is reported as ``0.0`` and the verdict is
``none`` - the caller is told the rate was never measured
(``LedgerEvidence.measured`` is false) instead of being told the semantic
path is healthy. Zero evidence is not evidence of zero.
"""
from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from .isolation import IsolationConfig, IsolationVerdict, evaluate_isolation

#: One action, as ``ActionLedger`` and the exports write it.
KIND_ENTRY = "entry"
#: A refusal that was written down without an entry of its own.
KIND_REFUSAL = "refusal"
#: The run's own claim about its numbers - a claim, not an action.
KIND_SUMMARY = "summary"
KINDS = (KIND_ENTRY, KIND_REFUSAL, KIND_SUMMARY)

#: The delivery path a refused record must carry: nothing was delivered.
DELIVERY_NONE = "none"

#: The flags a counted record has to carry, and that have to be booleans.
#: Same three the interference report insists on, so both tools accept and
#: reject the same files.
FLAGS = ("physical_input", "focus_stolen", "verified")

#: The window that means "the whole ledger" - the behaviour before
#: ``window_seconds`` existed, and still the default.
WINDOW_WHOLE_LEDGER = 0.0

#: Where each ``evaluate_isolation`` parameter comes from - repeated in the
#: JSON output so a reviewer does not have to read this module to check it.
MAPPING = {
    "semantic_miss_rate": "physical_inputs / entries",
    "budget_exhausted": "refusals within window_seconds (0 = the whole ledger)",
    "needs_parallel": "caller (never the ledger)",
    "concurrent_writers": "caller (never the ledger)",
}


class LedgerIsolationError(ValueError):
    """The ledger cannot be turned into evidence.

    Raised instead of skipping the offending records: a skipped record
    shrinks the denominator and inflates the rate, which is exactly the
    kind of silent drift the interference report exists to catch.
    """

    def __init__(self, problems: list[dict[str, Any]]) -> None:
        self.problems = problems
        summary = "; ".join(
            f"record {problem['record']}: {problem['problem']}"
            + (f" [{problem['field']}]" if problem.get("field") else "")
            for problem in problems[:5])
        more = "" if len(problems) <= 5 else f" (+{len(problems) - 5} more)"
        super().__init__(f"the ledger cannot be counted: {summary}{more}")


@dataclass(frozen=True)
class LedgerEvidence:
    """What the ledger measured, ready for ``evaluate_isolation``.

    Every number here is a count of records, so a reviewer can recount it
    with ``examples/interference-report.py`` and get the same totals.
    ``measured`` distinguishes "the rate was 0" from "there was nothing
    to measure".
    """

    #: Counted records: entry records plus standalone refusal records.
    entries: int = 0
    #: Records that drove the user's pointer.
    physical_inputs: int = 0
    #: Records an attention or focus budget refused, inside the window.
    refusals: int = 0
    #: The same count over the whole ledger, so a window cannot hide what
    #: it dropped. Equal to ``refusals`` when no window was asked for.
    refusals_total: int = 0
    #: Passed through from the caller, never inferred from the ledger.
    needs_parallel: bool = False
    #: Passed through from the caller, never inferred from the ledger.
    concurrent_writers: int = 1
    #: entries / physical_inputs / refusals per agent, for human review.
    agents: dict[str, dict[str, int]] = field(default_factory=dict)
    #: Summary claims seen and skipped: they describe the run, they are not
    #: actions and must not enter the denominator.
    claims_ignored: int = 0
    #: The window the refusals were counted in; 0 means the whole ledger.
    window_seconds: float = WINDOW_WHOLE_LEDGER
    #: Newest ``ts`` in the ledger, the anchor the window is measured back
    #: from. ``None`` when the ledger has nothing counted in it.
    window_end: float | None = None
    #: ``window_end - window_seconds``; refusals at exactly this ``ts`` are
    #: inside the window (the start is inclusive, like the thresholds).
    window_start: float | None = None

    @property
    def semantic_miss_rate(self) -> float:
        """``physical_inputs / entries``, and ``0.0`` when nothing was counted.

        The quotient is not rounded: rounding is a display decision, and a
        rate that rounds up to the threshold should not escalate a desktop.
        """
        return self.physical_inputs / self.entries if self.entries else 0.0

    @property
    def budget_exhausted(self) -> int:
        """The refusals, under the name ``evaluate_isolation`` uses."""
        return self.refusals

    @property
    def measured(self) -> bool:
        """Was there anything to measure? ``0/0`` is not a measurement."""
        return self.entries > 0

    def as_evaluate_kwargs(self) -> dict[str, Any]:
        """The four parameters, in the order ``evaluate_isolation`` takes them."""
        return {
            "needs_parallel": self.needs_parallel,
            "semantic_miss_rate": self.semantic_miss_rate,
            "budget_exhausted": self.budget_exhausted,
            "concurrent_writers": self.concurrent_writers,
        }

    def to_dict(self) -> dict[str, Any]:
        """Plain data, including where each number came from."""
        return {
            "entries": self.entries,
            "physical_inputs": self.physical_inputs,
            "refusals": self.refusals,
            "refusals_total": self.refusals_total,
            "window_seconds": self.window_seconds,
            # The same number under the name the tool's JSON contract uses.
            # ``window_seconds`` keeps this document snake_case like every
            # other key; both are written from one field, so they cannot
            # drift.
            "windowSeconds": self.window_seconds,
            "window_start": self.window_start,
            "window_end": self.window_end,
            "semantic_miss_rate": self.semantic_miss_rate,
            "budget_exhausted": self.budget_exhausted,
            "needs_parallel": self.needs_parallel,
            "concurrent_writers": self.concurrent_writers,
            "measured": self.measured,
            "claims_ignored": self.claims_ignored,
            "agents": {agent: dict(counts)
                       for agent, counts in sorted(self.agents.items())},
            "mapping": dict(MAPPING),
        }


def _field(record: Any, name: str, default: Any = None) -> Any:
    """One field of a record, whether it is a dict or a ``LedgerEntry``."""
    if isinstance(record, Mapping):
        return record.get(name, default)
    return getattr(record, name, default)


def _records_of(entries: Any) -> list[Any]:
    """Accept a list of records, or an ``ActionLedger`` that holds them."""
    getter = getattr(entries, "entries", None)
    if callable(getter):
        return list(getter())
    if isinstance(entries, Iterable) and not isinstance(entries, (str, bytes)):
        return list(entries)
    raise TypeError("entries must be a list of records or an ActionLedger")


def _problem(index: int, problem: str, field_name: str = "",
             value: Any = None) -> dict[str, Any]:
    row: dict[str, Any] = {"record": index, "problem": problem}
    if field_name:
        row["field"] = field_name
    if value is not None:
        row["value"] = value
    return row


def _timestamp(record: Any, index: int,
               problems: list[dict[str, Any]]) -> float | None:
    """A counted record's ``ts`` as seconds, or ``None`` after naming why not.

    Only asked for when a window is wanted: without one, ``ts`` is a label
    on a record that nothing reads, and a label must not reject a ledger.
    A record that cannot be placed on the timeline is reported - record
    index and field - rather than dropped, for the same reason a record
    without an ``agent_id`` is: a silently skipped record shrinks a count
    nobody can see shrinking.
    """
    value = _field(record, "ts", None)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        problems.append(_problem(
            index, "the record has no numeric ts, so the window cannot place "
                   "it on the timeline", "ts", value))
        return None
    number = float(value)
    if not math.isfinite(number):
        problems.append(_problem(
            index, "ts is not a finite number, so the window cannot place it "
                   "on the timeline", "ts", value))
        return None
    return number


def ledger_evidence(entries: Any, *, needs_parallel: bool = False,
                    concurrent_writers: int = 1,
                    window_seconds: float = WINDOW_WHOLE_LEDGER) -> LedgerEvidence:
    """Count the ledger into the numbers the isolation verdict reads.

    ``entries`` is a list of records (dicts, as the exports write them) or
    an ``ActionLedger``. ``needs_parallel`` and ``concurrent_writers`` are
    the caller's own knowledge about the work, not something this function
    can find in a ledger of past actions.

    ``window_seconds`` slices the *refusals* to the last N seconds of the
    ledger, anchored on the newest ``ts`` in it (see the module docstring
    for why ``entries`` is not sliced with them). ``0`` counts the whole
    ledger, which is what this function did before the window existed.

    Raises ``LedgerIsolationError`` when a record cannot be counted - a
    missing ``agent_id``, a flag that is not a boolean, a refusal that
    claims a delivery, or (with a window) a ``ts`` that is not a finite
    number. Refusals are checked by the same rules as
    ``examples/interference-report.py``, so both tools agree on which
    ledgers are readable.
    """
    window = float(window_seconds)
    if not math.isfinite(window) or window < 0:
        raise ValueError("window_seconds must be a finite number >= 0; "
                         "0 means the whole ledger")
    windowed = window > 0

    records = _records_of(entries)
    problems: list[dict[str, Any]] = []
    counted = 0
    physical = 0
    claims = 0
    agents: dict[str, dict[str, int]] = {}
    #: One ``(ts, agent)`` per refusal, so the window can be applied once
    #: the anchor is known instead of twice inside the loop.
    refusals: list[tuple[float, str]] = []
    #: Every counted record's ``ts``; the newest of them anchors the window.
    timestamps: list[float] = []

    for index, record in enumerate(records):
        kind = _field(record, "kind", KIND_ENTRY)
        if kind not in KINDS:
            problems.append(_problem(index, f"unknown record kind: {kind!r}",
                                     "kind", kind))
            continue
        if kind == KIND_SUMMARY:
            claims += 1
            continue

        agent = _field(record, "agent_id", None)
        if not isinstance(agent, str) or not agent.strip():
            problems.append(_problem(index, "the record names no agent_id",
                                     "agent_id", agent))
            continue
        counts = agents.setdefault(
            agent, {"entries": 0, "physical_inputs": 0, "refusals": 0,
                    "refusals_total": 0})

        if windowed:
            ts = _timestamp(record, index, problems)
            if ts is None:
                continue
            timestamps.append(ts)
        else:
            ts = 0.0

        if kind == KIND_REFUSAL:
            counted += 1
            counts["entries"] += 1
            counts["refusals_total"] += 1
            refusals.append((ts, agent))
            continue

        flags: dict[str, bool] = {}
        for name in FLAGS:
            value = _field(record, name, None)
            if not isinstance(value, bool):
                problems.append(_problem(index, "the flag is not a boolean",
                                         name, value))
                break
            flags[name] = value
        else:
            ok = _field(record, "ok", True)
            refused = _field(record, "refused", False)
            if not isinstance(ok, bool):
                problems.append(_problem(index, "ok is not a boolean", "ok", ok))
                continue
            if not isinstance(refused, bool):
                problems.append(_problem(index, "refused must be a boolean",
                                         "refused", refused))
                continue
            if refused:
                delivery = _field(record, "delivery", DELIVERY_NONE)
                wrong = []
                if delivery != DELIVERY_NONE:
                    wrong.append(f"delivery={delivery!r}")
                if ok:
                    wrong.append("ok=true")
                if flags["verified"]:
                    wrong.append("verified=true")
                if wrong:
                    problems.append(_problem(
                        index, "a refused entry must not claim a delivery or "
                               "a result", "refused", ", ".join(wrong)))
                    continue
                refusals.append((ts, agent))
                counts["refusals_total"] += 1

            counted += 1
            counts["entries"] += 1
            if flags["physical_input"]:
                physical += 1
                counts["physical_inputs"] += 1

    if problems:
        raise LedgerIsolationError(problems)

    window_start = window_end = None
    if windowed and timestamps:
        window_end = max(timestamps)
        window_start = window_end - window

    if windowed:
        # No anchor means nothing was counted, so nothing can be inside a
        # window either. The start is inclusive: a refusal whose ts is
        # exactly ``latest_ts - N`` is in, the same inclusive edge every
        # threshold in ``evaluate_isolation`` uses.
        kept = [] if window_start is None else [
            mark for mark in refusals if mark[0] >= window_start]
    else:
        kept = refusals
    for _ts, agent in kept:
        agents[agent]["refusals"] += 1

    return LedgerEvidence(entries=counted, physical_inputs=physical,
                          refusals=len(kept),
                          refusals_total=len(refusals),
                          needs_parallel=bool(needs_parallel),
                          concurrent_writers=int(concurrent_writers),
                          agents=agents, claims_ignored=claims,
                          window_seconds=window,
                          window_end=window_end,
                          window_start=window_start)


def isolation_verdict(entries: Any, *, needs_parallel: bool = False,
                      concurrent_writers: int = 1,
                      window_seconds: float = WINDOW_WHOLE_LEDGER,
                      config: IsolationConfig | None = None) -> IsolationVerdict:
    """The verdict for this ledger: stay on the shared desktop, or leave it.

    A thin hand-off, on purpose: the evidence is counted here, and the
    thresholds are applied by ``evaluate_isolation`` unchanged. Threshold
    semantics - inclusive edges, vm outranking session, the reasons - are
    its business and are not re-implemented here.
    """
    evidence = ledger_evidence(entries, needs_parallel=needs_parallel,
                               concurrent_writers=concurrent_writers,
                               window_seconds=window_seconds)
    return evaluate_isolation(config=config, **evidence.as_evaluate_kwargs())


def isolation_report(entries: Any, *, needs_parallel: bool = False,
                     concurrent_writers: int = 1,
                     window_seconds: float = WINDOW_WHOLE_LEDGER,
                     config: IsolationConfig | None = None,
                     ledger: str = "") -> dict[str, Any]:
    """Verdict plus evidence, as one JSON-able document for human review.

    The point of shipping the evidence alongside the verdict is that a
    reader can check the numbers instead of trusting the sentence they
    produced: every count in ``evidence`` can be recounted from the same
    ledger with ``examples/interference-report.py`` - the whole-ledger ones
    always, the windowed refusals over the window the evidence names.
    """
    evidence = ledger_evidence(entries, needs_parallel=needs_parallel,
                               concurrent_writers=concurrent_writers,
                               window_seconds=window_seconds)
    verdict = evaluate_isolation(config=config, **evidence.as_evaluate_kwargs())
    return {
        "ledger": ledger,
        "verdict": verdict.to_dict(),
        "evidence": evidence.to_dict(),
    }
