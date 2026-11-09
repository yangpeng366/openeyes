"""The action ledger: what the automation did, and what it cost the user.

The supervision loop already knows two things about every round - whether
the action was delivered, and whether it disturbed the human sharing the
desktop. It does not keep them: each run returns a report that is thrown
away once the round ends.

This module is where a run leaves a trace. An ``ActionLedger`` is
append-only and inert - it records, it never intercepts and never
raises. Later stages read it to answer two questions: *what did we do*
and *how much of it touched the user*.

Three facts are recorded per action, and they are deliberately separate:

* **delivery** - how the action reached the app: ``semantic`` (an
  accessibility call), ``message`` (a posted window message), ``pixel``
  (a synthesized pointer event), ``foreground`` (a focus-stealing
  physical action) or ``none`` (nothing was delivered).
* **physical input** - whether the automation drove the user's pointer.
  Only a delivered pixel action counts; the user moving their own mouse
  never does.
* **verified** - whether the expected state was observed afterwards. A
  delivered action is not a successful one.

Every entry carries the ``agent_id`` that produced it, so several agents
can share one ledger and still be told apart afterwards.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

#: The action reached the app through an accessibility call.
DELIVERY_SEMANTIC = "semantic"
#: The action reached the app through a posted window message.
DELIVERY_MESSAGE = "message"
#: The action reached the app through a synthesized pointer event.
DELIVERY_PIXEL = "pixel"
#: The action reached the app through physical input that took the focus.
DELIVERY_FOREGROUND = "foreground"
#: Nothing was delivered.
DELIVERY_NONE = "none"

#: How a backend's ``method`` maps onto a delivery path.
_METHOD_DELIVERY = {
    "invoke": DELIVERY_SEMANTIC,
    "value": DELIVERY_SEMANTIC,
    "set_value": DELIVERY_SEMANTIC,
    "message": DELIVERY_MESSAGE,
    "postmessage": DELIVERY_MESSAGE,
    "pixel": DELIVERY_PIXEL,
    "foreground": DELIVERY_FOREGROUND,
}


def delivery_for_method(method: str | None) -> str:
    """Name the delivery path a backend ``method`` belongs to.

    An unknown or absent method is ``none``: the ledger would rather say
    "we do not know how this was delivered" than guess a cheaper path
    than the truth.
    """
    if not method:
        return DELIVERY_NONE
    return _METHOD_DELIVERY.get(str(method).lower(), DELIVERY_NONE)


@dataclass
class LedgerEntry:
    """One recorded action."""

    agent_id: str
    action: str
    delivery: str = DELIVERY_NONE
    physical_input: bool = False
    focus_stolen: bool = False
    cursor_moved: bool = False
    verified: bool = False
    ok: bool = True
    target: str = ""
    detail: str = ""
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "action": self.action,
            "delivery": self.delivery,
            "physical_input": self.physical_input,
            "focus_stolen": self.focus_stolen,
            "cursor_moved": self.cursor_moved,
            "verified": self.verified,
            "ok": self.ok,
            "target": self.target,
            "detail": self.detail,
            "ts": self.ts,
        }


class ActionLedger:
    """An append-only record of what the agents did.

    The ledger is inert: ``record`` stores, ``summary`` reads back. It
    never raises on a surprising input and never reaches outside itself,
    so it is safe to keep in the path of a running loop.
    """

    def __init__(self, agent_id: str = "default") -> None:
        self.agent_id = agent_id
        self._entries: list[LedgerEntry] = []

    def record(self, *, action: str, delivery: str = DELIVERY_NONE,
               physical_input: bool = False, focus_stolen: bool = False,
               cursor_moved: bool = False, verified: bool = False,
               ok: bool = True, target: str = "", detail: str = "",
               agent_id: str | None = None) -> LedgerEntry:
        """Append one action and return the entry that was written."""
        entry = LedgerEntry(
            agent_id=agent_id or self.agent_id,
            action=action,
            delivery=delivery,
            physical_input=bool(physical_input),
            focus_stolen=bool(focus_stolen),
            cursor_moved=bool(cursor_moved),
            verified=bool(verified),
            ok=bool(ok),
            target=target,
            detail=detail,
        )
        self._entries.append(entry)
        return entry

    def entries(self) -> list[LedgerEntry]:
        """A copy of what has been recorded, oldest first."""
        return list(self._entries)

    def summary(self) -> dict[str, Any]:
        """Counts over everything recorded so far."""
        deliveries: dict[str, int] = {}
        kinds: dict[str, int] = {}
        for entry in self._entries:
            deliveries[entry.delivery] = deliveries.get(entry.delivery, 0) + 1
            kinds[entry.action] = kinds.get(entry.action, 0) + 1
        actions = len(self._entries)
        physical = sum(1 for e in self._entries if e.physical_input)
        return {
            "agent_id": self.agent_id,
            "actions": actions,
            "physical_inputs": physical,
            "physical_input_rate": round(physical / actions, 4) if actions else 0.0,
            "focus_stolen": sum(1 for e in self._entries if e.focus_stolen),
            "cursor_moves": sum(1 for e in self._entries if e.cursor_moved),
            "verified": sum(1 for e in self._entries if e.verified),
            "ok": sum(1 for e in self._entries if e.ok),
            "deliveries": deliveries,
            "actions_by_kind": kinds,
        }