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
  than counting it. *Limit*: the ledger carries timestamps but no
  window. ``budget_exhausted`` is a count over the whole ledger handed
  in, while the threshold in ``IsolationConfig`` is documented as "per
  minute". Slicing the ledger to the window the caller means (for
  example the last minute) is the caller's job; this module will not
  invent a window from ``ts``, because a silently truncated window would
  make two runs incomparable.

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

#: Where each ``evaluate_isolation`` parameter comes from - repeated in the
#: JSON output so a reviewer does not have to read this module to check it.
MAPPING = {
    "semantic_miss_rate": "physical_inputs / entries",
    "budget_exhausted": "refusals",
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
    #: Records an attention or focus budget refused.
    refusals: int = 0
    #: Passed through from the caller, never inferred from the ledger.
    needs_parallel: bool = False
    #: Passed through from the caller, never inferred from the ledger.
    concurrent_writers: int = 1
    #: entries / physical_inputs / refusals per agent, for human review.
    agents: dict[str, dict[str, int]] = field(default_factory=dict)
    #: Summary claims seen and skipped: they describe the run, they are not
    #: actions and must not enter the denominator.
    claims_ignored: int = 0

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


def ledger_evidence(entries: Any, *, needs_parallel: bool = False,
                    concurrent_writers: int = 1) -> LedgerEvidence:
    """Count the ledger into the numbers the isolation verdict reads.

    ``entries`` is a list of records (dicts, as the exports write them) or
    an ``ActionLedger``. ``needs_parallel`` and ``concurrent_writers`` are
    the caller's own knowledge about the work, not something this function
    can find in a ledger of past actions.

    Raises ``LedgerIsolationError`` when a record cannot be counted - a
    missing ``agent_id``, a flag that is not a boolean, or a refusal that
    claims a delivery. Refusals are checked by the same rules as
    ``examples/interference-report.py``, so both tools agree on which
    ledgers are readable.
    """
    records = _records_of(entries)
    problems: list[dict[str, Any]] = []
    counted = 0
    physical = 0
    refusals = 0
    claims = 0
    agents: dict[str, dict[str, int]] = {}

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
            agent, {"entries": 0, "physical_inputs": 0, "refusals": 0})

        if kind == KIND_REFUSAL:
            counted += 1
            refusals += 1
            counts["entries"] += 1
            counts["refusals"] += 1
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
                refusals += 1
                counts["refusals"] += 1

            counted += 1
            counts["entries"] += 1
            if flags["physical_input"]:
                physical += 1
                counts["physical_inputs"] += 1

    if problems:
        raise LedgerIsolationError(problems)

    return LedgerEvidence(entries=counted, physical_inputs=physical,
                          refusals=refusals,
                          needs_parallel=bool(needs_parallel),
                          concurrent_writers=int(concurrent_writers),
                          agents=agents, claims_ignored=claims)


def isolation_verdict(entries: Any, *, needs_parallel: bool = False,
                      concurrent_writers: int = 1,
                      config: IsolationConfig | None = None) -> IsolationVerdict:
    """The verdict for this ledger: stay on the shared desktop, or leave it.

    A thin hand-off, on purpose: the evidence is counted here, and the
    thresholds are applied by ``evaluate_isolation`` unchanged. Threshold
    semantics - inclusive edges, vm outranking session, the reasons - are
    its business and are not re-implemented here.
    """
    evidence = ledger_evidence(entries, needs_parallel=needs_parallel,
                               concurrent_writers=concurrent_writers)
    return evaluate_isolation(config=config, **evidence.as_evaluate_kwargs())


def isolation_report(entries: Any, *, needs_parallel: bool = False,
                     concurrent_writers: int = 1,
                     config: IsolationConfig | None = None,
                     ledger: str = "") -> dict[str, Any]:
    """Verdict plus evidence, as one JSON-able document for human review.

    The point of shipping the evidence alongside the verdict is that a
    reader can check the numbers instead of trusting the sentence they
    produced: every count in ``evidence`` can be recounted from the same
    ledger with ``examples/interference-report.py``.
    """
    evidence = ledger_evidence(entries, needs_parallel=needs_parallel,
                               concurrent_writers=concurrent_writers)
    verdict = evaluate_isolation(config=config, **evidence.as_evaluate_kwargs())
    return {
        "ledger": ledger,
        "verdict": verdict.to_dict(),
        "evidence": evidence.to_dict(),
    }
