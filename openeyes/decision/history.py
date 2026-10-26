"""The labelled history behind the local XERJ judge.

A local XERJ node answers `POST /v1/systemone` with a rank-weighted vote
over a labelled history: one document per example, carrying the fields the
node's `[decisions]` settings name (`label`, `text`). The node retrieves
neighbours by the question text and votes on their labels, so a document has
to hold both the goal phrasing it should match and the option names it must
be told apart from - hence the text convention `<goal> | <option> | ...`.

The history lives here as data, so the same examples drive both the seeding
of the node (`examples/seed-systemone-history.py`) and the measurement of
its votes (`examples/systemone-calibration.py`).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

SEED_PATH = Path(__file__).with_name("history_seed.json")
PROBE_PATH = Path(__file__).with_name("history_probe.json")


@dataclass(frozen=True)
class HistoryExample:
    """One labelled example: the option it selects, the goal that asks for
    it, and the options it must be told apart from."""

    id: str
    label: str
    goal: str
    distractors: tuple[str, ...]
    lang: str = ""

    @property
    def text(self) -> str:
        return " | ".join((self.goal, self.label, *self.distractors))

    @property
    def candidates(self) -> tuple[str, ...]:
        return (self.label, *self.distractors)


def load_seed(path: Path | None = None) -> list[HistoryExample]:
    """Read the labelled history seed."""
    return _load(path or SEED_PATH)


def load_probe(path: Path | None = None) -> list[HistoryExample]:
    """Read the held-out goals: the same options, phrased differently.

    A vote on the seed only shows that the mechanism works - the document
    it matches is the one the seed was built from. The probe is where the
    judge has to generalise, so it is the set the thresholds are measured on.
    """
    return _load(path or PROBE_PATH)


def _load(path: Path) -> list[HistoryExample]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [
        HistoryExample(
            id=item["id"],
            label=item["label"],
            goal=item["goal"],
            distractors=tuple(item["distractors"]),
            lang=item.get("lang", ""),
        )
        for item in raw["examples"]
    ]


def to_records(examples: list[HistoryExample]) -> list[dict]:
    """The node's NDJSON documents for a set of examples."""
    return [{"label": example.label, "text": example.text} for example in examples]
