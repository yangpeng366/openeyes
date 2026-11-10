"""How much did that run disturb the human? Four numbers, and no guessing.

``docs/mouse-cluster.md`` §6 step 6 asks one question, in a form that can
be answered without trusting anybody: after a run, how many times did the
automation touch the physical pointer (``physical_input``), how many times
did it take the user's foreground window (``focus_stolen``), how many
interruptions did the attention budget stop (``refused``), and how many
actions were actually verified (``verified``)? The report answers it and
then proves it did not make the numbers up - it recounts every one of
them from the raw entries and compares the totals with the claim the run
itself wrote.

That comparison is the point. A summary that is printed and never
checked is a summary that drifts: the moment the counter in the loop and
the ledger in the file disagree, the printed number is a claim about a
run that did not happen. This tool exits non-zero and prints the
difference instead of picking one of the two.

Input: a ledger export, either

* **JSONL** - one record per line, the form ``examples/mouse-cluster-demo.py``
  writes: ``{"kind": "entry", ...}`` records, optional
  ``{"kind": "refusal", "agent_id": ...}`` records, and one
  ``{"kind": "summary", ...}`` claim; or
* **a JSON array** of entry objects (``kind`` may be omitted; plain entries
  are the default), or a JSON object with ``entries`` / ``refusals`` /
  ``summary`` keys.

The counting rules, spelled out so the numbers mean something:

* ``total`` is every entry record - an attempt is an attempt, whether it
  was refused or drawn. ``verified/total`` is therefore the share of
  attempts whose outcome was observed, not the share that succeeded.
* ``refused`` counts an entry that carries ``"refused": true``, or a
  standalone refusal record. A refusal is not a delivery: it must carry
  ``delivery == "none"``, ``ok == false`` and ``verified == false``. A file
  that says otherwise is inconsistent, and inconsistency is an error, not
  a rounding difference.
* a record without an ``agent_id`` cannot be attributed to an agent, so it
  is an error too - the whole point of a shared ledger is that the agents
  can be told apart in it.

Run it:

    python examples\\interference-report.py --ledger examples\\out\\mouse-cluster-ledger.jsonl
    python examples\\interference-report.py --ledger export.json --json

Exit codes: 0 the claim matches the entries (or there is no claim); 1 a
claim disagrees with the entries; 2 the ledger cannot be read or is
inconsistent.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: The four numbers the report exists for, in the order it prints them.
METRICS = ("physical_input", "focus_stolen", "refused", "verified")

KIND_ENTRY = "entry"
KIND_REFUSAL = "refusal"
KIND_SUMMARY = "summary"
KINDS = (KIND_ENTRY, KIND_REFUSAL, KIND_SUMMARY)

#: The delivery path a refused action must carry: nothing was delivered.
DELIVERY_NONE = "none"


class LedgerFormatError(ValueError):
    """The file is not a ledger this report can read."""


# ------------------------------------------------------------------ counting
@dataclass
class Counts:
    """The four numbers, plus how many entries they were counted over."""

    entries: int = 0
    physical_input: int = 0
    focus_stolen: int = 0
    refused: int = 0
    verified: int = 0

    def add(self, other: Counts) -> None:
        self.entries += other.entries
        for name in METRICS:
            setattr(self, name, getattr(self, name) + getattr(other, name))

    def get(self, name: str) -> int:
        """One number by name; ``entries`` is a number like the others."""
        return int(getattr(self, name))

    @property
    def verified_ratio(self) -> float:
        return self.verified / self.entries if self.entries else 0.0

    def as_dict(self) -> dict[str, int]:
        return {"entries": self.entries,
                **{name: self.get(name) for name in METRICS}}


@dataclass
class Report:
    """What the ledger says, and every place a claim disagreed with it."""

    path: str = ""
    totals: Counts = field(default_factory=Counts)
    agents: dict[str, Counts] = field(default_factory=dict)
    claims: list[dict[str, Any]] = field(default_factory=list)
    diffs: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    records: dict[str, int] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """The entries are readable and every claim matches them."""
        return not self.errors and not self.diffs

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "ledger": self.path,
            "records": dict(self.records),
            "totals": {**self.totals.as_dict(),
                       "verified_ratio": round(self.totals.verified_ratio, 4)},
            "agents": {agent: counts.as_dict()
                       for agent, counts in self.agents.items()},
            "claims": self.claims,
            "diffs": self.diffs,
            "errors": self.errors,
        }


def _error(record: int, problem: str, field_name: str = "",
           value: Any = None) -> dict[str, Any]:
    row: dict[str, Any] = {"record": record, "problem": problem}
    if field_name:
        row["field"] = field_name
    if value is not None:
        row["value"] = value
    return row


def _is_int(value: Any) -> bool:
    """A count is an int - and ``True`` is not a count, whatever Python says."""
    return isinstance(value, int) and not isinstance(value, bool)


# -------------------------------------------------------------------- loading
def load_records(path: str | Path) -> list[dict[str, Any]]:
    """Read a ledger export into records, whichever of the forms it uses.

    Raises ``LedgerFormatError`` when the file is not JSON at all, and
    ``OSError`` when it cannot be read.
    """
    text = Path(path).read_text(encoding="utf-8")
    if not text.strip():
        raise LedgerFormatError("the ledger is empty")

    head = text.lstrip()[:1]
    if head in "[{":
        try:
            document = json.loads(text)
        except json.JSONDecodeError:
            if head == "[":
                raise LedgerFormatError(
                    "the ledger looks like a JSON array but does not parse") from None
            # A JSONL file whose first character happens to be ``{``.
            return _load_jsonl(text)
        if isinstance(document, list):
            return [record for record in document]
        if isinstance(document, dict):
            return _records_from_document(document)
        raise LedgerFormatError(
            f"a ledger record must be an object, not {type(document).__name__}")
    return _load_jsonl(text)


def _load_jsonl(text: str) -> list[dict[str, Any]]:
    """One JSON object per line; blank lines are not records."""
    records: list[dict[str, Any]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise LedgerFormatError(
                f"line {number} is not JSON: {exc.msg}") from None
        if not isinstance(record, dict):
            raise LedgerFormatError(
                f"line {number} is a {type(record).__name__}, not an object")
        records.append(record)
    return records


def _records_from_document(document: dict[str, Any]) -> list[dict[str, Any]]:
    """A whole-document export: ``entries``, ``refusals`` and ``summary``."""
    if "entries" in document or "records" in document:
        entries = document.get("entries", document.get("records"))
        if not isinstance(entries, list):
            raise LedgerFormatError("'entries' must be a list of records")
        records: list[dict[str, Any]] = []
        for item in entries:
            if not isinstance(item, dict):
                raise LedgerFormatError("every entry must be an object")
            record = dict(item)
            record.setdefault("kind", KIND_ENTRY)
            records.append(record)
        refusals = document.get("refusals", [])
        if not isinstance(refusals, list):
            raise LedgerFormatError("'refusals' must be a list")
        for item in refusals:
            record = item if isinstance(item, dict) else {"agent_id": item}
            records.append({"kind": KIND_REFUSAL, **record})
        summary = document.get("summary")
        if summary is not None:
            if not isinstance(summary, dict):
                raise LedgerFormatError("'summary' must be an object")
            records.append({"kind": KIND_SUMMARY, **summary})
        return records
    return [document]


# ------------------------------------------------------------------ the recount
def summarise(records: list[dict[str, Any]], path: str = "") -> Report:
    """Recount the four numbers from the records, then check the claims.

    Nothing here trusts a stored total: every number is added up entry by
    entry, and a ``summary`` record is compared against that recount.
    """
    report = Report(path=str(path))
    report.records = {"entries": 0, "refusals": 0, "summaries": 0}
    counted = 0

    for index, record in enumerate(records):
        kind = record.get("kind", KIND_ENTRY)
        if kind not in KINDS:
            report.errors.append(_error(index, f"unknown record kind: {kind!r}",
                                        "kind", kind))
            continue
        if kind == KIND_SUMMARY:
            report.records["summaries"] += 1
            report.claims.append(record)
            continue
        if kind == KIND_REFUSAL:
            agent = _agent_of(record, index, report)
            if agent is None:
                continue
            report.records["refusals"] += 1
            counts = report.agents.setdefault(agent, Counts())
            counts.entries += 1
            counts.refused += 1
            counted += 1
            continue

        agent = _agent_of(record, index, report)
        if agent is None:
            continue
        flags = _flags_of(record, index, report)
        if flags is None:
            continue
        refused = record.get("refused", False)
        if not isinstance(refused, bool):
            report.errors.append(_error(index, "refused must be a boolean",
                                        "refused", refused))
            continue
        if refused and not _refusal_is_consistent(record, index, report, flags):
            continue

        counts = report.agents.setdefault(agent, Counts())
        counts.entries += 1
        for name in ("physical_input", "focus_stolen", "verified"):
            if flags[name]:
                setattr(counts, name, getattr(counts, name) + 1)
        if refused:
            counts.refused += 1
        report.records["entries"] += 1
        counted += 1

    for counts in report.agents.values():
        report.totals.add(counts)

    # A second tally, done a different way: if these disagree with the
    # per-agent sum, one of the two passes is wrong and the numbers are
    # not to be trusted.
    if report.totals.entries != counted:
        report.errors.append(_error(
            -1, "the per-agent totals do not add up to the records counted",
            "entries", {"agents": report.totals.entries, "records": counted}))

    _check_claims(report)
    return report


def _agent_of(record: dict[str, Any], index: int, report: Report) -> str | None:
    """Every record has to name the agent it belongs to."""
    agent = record.get("agent_id")
    if not isinstance(agent, str) or not agent.strip():
        report.errors.append(_error(index, "the record names no agent_id",
                                    "agent_id", agent))
        return None
    return agent


def _flags_of(record: dict[str, Any], index: int,
              report: Report) -> dict[str, bool] | None:
    """The three boolean flags, which must be present and really boolean."""
    flags: dict[str, bool] = {}
    for name in ("physical_input", "focus_stolen", "verified"):
        if name not in record:
            report.errors.append(_error(index, "the record is missing a flag",
                                        name))
            return None
        value = record[name]
        if not isinstance(value, bool):
            report.errors.append(_error(index, "the flag is not a boolean",
                                        name, value))
            return None
        flags[name] = value
    ok = record.get("ok", True)
    if not isinstance(ok, bool):
        report.errors.append(_error(index, "ok is not a boolean", "ok", ok))
        return None
    return flags


def _refusal_is_consistent(record: dict[str, Any], index: int, report: Report,
                           flags: dict[str, bool]) -> bool:
    """A refusal delivered nothing, succeeded at nothing, verified nothing."""
    problems = []
    delivery = record.get("delivery", DELIVERY_NONE)
    if delivery != DELIVERY_NONE:
        problems.append(f"delivery={delivery!r}")
    if record.get("ok", True):
        problems.append("ok=true")
    if flags["verified"]:
        problems.append("verified=true")
    if problems:
        report.errors.append(_error(
            index, "a refused entry must not claim a delivery or a result",
            "refused", ", ".join(problems)))
        return False
    return True


def _check_claims(report: Report) -> None:
    """Compare every stored claim with the recount, field by field."""
    for index, claim in enumerate(report.claims):
        if len(report.claims) > 1:
            report.errors.append(_error(
                index, "more than one summary claim: the report cannot tell "
                       "which one is meant"))
            return
        _compare_scope(report, index, "total", claim, report.totals)
        agents = claim.get("agents")
        if agents is None:
            continue
        if not isinstance(agents, dict):
            report.errors.append(_error(index, "the claim's 'agents' is not an "
                                               "object", "agents", agents))
            continue
        for agent, claimed in agents.items():
            if not isinstance(claimed, dict):
                report.errors.append(_error(index, "an agent claim is not an "
                                                   "object", "agents", agent))
                continue
            _compare_scope(report, index, agent, claimed,
                           report.agents.get(agent, Counts()))
        for agent, counts in report.agents.items():
            if agent not in agents:
                report.diffs.append({"scope": agent, "field": "entries",
                                     "claimed": None,
                                     "counted": counts.entries})


def _compare_scope(report: Report, index: int, scope: str,
                   claim: dict[str, Any], counted: Counts) -> None:
    """One claim against one set of counted numbers."""
    for name in ("entries", *METRICS):
        if name not in claim:
            continue
        claimed = claim[name]
        if not _is_int(claimed):
            report.errors.append(_error(index, "a claimed count is not an int",
                                        name, claimed))
            continue
        actual = counted.get(name)
        if claimed != actual:
            report.diffs.append({"scope": scope, "field": name,
                                 "claimed": claimed, "counted": actual})


# --------------------------------------------------------------------- output
def render(report: Report) -> str:
    """The human-readable report: four numbers, then how they were checked."""
    lines = [f"ledger: {report.path or '<memory>'}",
             f"records: {report.records.get('entries', 0)} entries, "
             f"{report.records.get('refusals', 0)} standalone refusals, "
             f"{report.records.get('summaries', 0)} summary claim(s)"]
    if report.agents:
        lines.append("agents:")
        width = max(len(agent) for agent in report.agents)
        for agent, counts in sorted(report.agents.items()):
            numbers = " ".join(f"{name}={counts.get(name)}" for name in METRICS)
            lines.append(f"  {agent:<{width}}  entries={counts.entries}  "
                         f"{numbers}")
    totals = report.totals
    lines.append("total: " + f"entries={totals.entries} "
                 + " ".join(f"{name}={totals.get(name)}" for name in METRICS))
    lines.append(f"verified/total: {totals.verified}/{totals.entries} = "
                 f"{totals.verified_ratio:.2%}")
    if report.errors:
        lines.append(f"inconsistent ledger: {len(report.errors)} problem(s)")
        for problem in report.errors:
            where = "record ?" if problem["record"] < 0 else f"record {problem['record']}"
            detail = f" [{problem['field']}]" if problem.get("field") else ""
            lines.append(f"  {where}{detail}: {problem['problem']}"
                         + (f" (value={problem['value']!r})"
                            if "value" in problem else ""))
    if report.diffs:
        lines.append(f"CLAIM MISMATCH: {len(report.diffs)} difference(s) "
                     f"between the stored summary and the entries")
        for diff in report.diffs:
            lines.append(f"  {diff['scope']}.{diff['field']}: "
                         f"claimed={diff['claimed']} "
                         f"counted={diff['counted']}")
    elif report.claims:
        lines.append("claim: matches the recount")
    if report.errors:
        lines.append("verdict: the ledger cannot be trusted until these are fixed")
    elif report.diffs:
        lines.append("verdict: the summary does not describe this ledger")
    else:
        lines.append("verdict: clean - every number above comes from the entries")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ledger", required=True,
                        help="a ledger export: JSONL, or a JSON array")
    parser.add_argument("--json", action="store_true",
                        help="print the report as JSON")
    args = parser.parse_args(argv)

    try:
        records = load_records(args.ledger)
    except (OSError, ValueError) as exc:
        message = f"cannot read the ledger {args.ledger}: {exc}"
        if args.json:
            print(json.dumps({"ok": False, "ledger": args.ledger,
                              "errors": [{"problem": str(exc)}]},
                             ensure_ascii=False, indent=2))
        else:
            print(message)
        return 2

    report = summarise(records, path=args.ledger)
    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(render(report))

    if report.errors:
        return 2
    if report.diffs:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
