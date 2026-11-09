"""Who an agent is, and what it may take from the user.

An agent that drives a desktop somebody else is using has to answer two
questions before every action: may I touch the pointer, and may I take
the foreground? The profile holds the answers; the budget holds the
running count.

Everything in this module is inert. It decides; it never acts.

The defaults are the safe ones: an agent acts normally, it may not take
the foreground, and it is counted either way. That is the difference
between "the automation touched my mouse" and "the automation was
allowed to touch my mouse, twice".
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

#: Read-only: the agent looks and reports, it never acts.
MODE_OBSERVE = "observe"
#: The normal mode: the agent acts.
MODE_ACT = "act"
#: Rehearsal: the agent plans and reports, but nothing is delivered.
MODE_SHADOW = "shadow"

#: The overlay palette. A colour is a function of the agent id, so the
#: same identity always looks the same on screen.
_PALETTE = ("#e6194b", "#3cb44b", "#4363d8", "#f58231", "#911eb4",
            "#42d4f4", "#f032e6", "#bfef45", "#fabed4", "#469990")


def color_for(agent_id: str) -> str:
    """A stable colour for an identity: the same id always looks the same."""
    digest = 0
    for ch in agent_id or "default":
        digest = (digest * 131 + ord(ch)) & 0xFFFFFFFF
    return _PALETTE[digest % len(_PALETTE)]


@dataclass
class AgentProfile:
    """Who an agent is, and what it may take from the user.

    Every field has a default, and the defaults are the conservative
    ones - an agent that never asked for anything gets a profile that
    does not take the foreground.
    """

    agent_id: str = "default"
    #: a short name for the overlay cursor
    label: str = ""
    #: the overlay colour; empty means "derive it from the agent id"
    color: str = ""
    #: one of MODE_OBSERVE / MODE_ACT / MODE_SHADOW
    mode: str = MODE_ACT
    #: how many times per minute this agent may take the foreground away
    #: from the user. Zero means it may not take it at all.
    focus_budget_per_min: int = 0
    #: the queue priority; larger goes first
    priority: int = 0
    #: may this agent bring a window to the front on purpose?
    allow_foreground: bool = False

    def __post_init__(self) -> None:
        if self.mode not in (MODE_OBSERVE, MODE_ACT, MODE_SHADOW):
            raise ValueError(f"unknown agent mode: {self.mode!r}")
        if self.focus_budget_per_min < 0:
            raise ValueError("focus_budget_per_min may not be negative")
        if not self.color:
            self.color = color_for(self.agent_id)

    @property
    def may_act(self) -> bool:
        """An observing agent never touches the desktop."""
        return self.mode == MODE_ACT

    def new_budget(self) -> FocusBudget:
        """A live budget matching this profile."""
        return FocusBudget(self.focus_budget_per_min)


class FocusBudget:
    """How often an agent may take the foreground away from the user.

    The budget counts *steals* in a sliding window of one minute. Zero
    means the agent may not take the foreground at all: the semantic
    path either works, or the action fails with a structured error. A
    positive number allows that many steals per minute.

    The budget is never "unmeasured": every attempt is counted, the
    refused ones included.
    """

    def __init__(self, per_minute: int = 0,
                 clock: Callable[[], float] = time.monotonic,
                 window_s: float = 60.0) -> None:
        self.per_minute = max(0, int(per_minute))
        self._clock = clock
        self._window_s = window_s
        self._stamps: list[float] = []
        #: how many steals were refused - the number a human would have
        #: been annoyed by
        self.refused = 0

    def _prune(self) -> None:
        cutoff = self._clock() - self._window_s
        self._stamps = [t for t in self._stamps if t > cutoff]

    @property
    def used(self) -> int:
        """Steals still inside the window."""
        self._prune()
        return len(self._stamps)

    @property
    def exhausted(self) -> bool:
        """True when there was a budget and it is used up."""
        return self.per_minute > 0 and self.used >= self.per_minute

    def allows(self) -> bool:
        """May one more steal happen now?"""
        if self.per_minute <= 0:
            return False
        self._prune()
        return len(self._stamps) < self.per_minute

    def spend(self) -> bool:
        """Record one steal. False when the budget says no."""
        if not self.allows():
            self.refused += 1
            return False
        self._stamps.append(self._clock())
        return True

    def to_dict(self) -> dict:
        return {
            "per_minute": self.per_minute,
            "used": self.used,
            "refused": self.refused,
            "exhausted": self.exhausted,
        }
