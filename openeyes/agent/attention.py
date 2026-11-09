"""How often an agent may interrupt the human sitting at the desk.

Several agents share one desktop, and the human is the scarcest thing
on it - not the mouse, not the screen, the human. An agent that wants
to say "I am done" costs the user a fragment of attention, and that
fragment does not come back.

So this budget counts *interruptions*, not actions: an agent may work
as long as it likes, but it may only ask to be looked at a bounded
number of times a minute. Zero means it may never ask - it keeps
working, it queues, and the user finds out when they come looking.

A refusal is not a non-event. The moments when an agent *would* have
interrupted and did not are the ones worth counting: they are what the
budget was bought for, and their number is the honest measure of how
much of the user's attention was protected.

The counter this budget keeps is the one the isolation layer reads:
``refused`` counts the interruptions the user did not have to
suffer. It is deliberately narrow. A request that was never made - an
agent with nothing to say - is not a refusal and does not count:
nobody was disturbed, so it is no evidence of anything.
``AgentPresence`` keeps its own counter beside this one, and that
one is wider: it counts every announcement that did not land,
including the ones that never wanted to interrupt anybody. Read
this counter for disturbance, not that one.

Everything here is inert. It counts; it never acts, and it never
raises on a surprising number - the caller decides what a refusal
means.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any


class AttentionBudget:
    """How many times an agent may interrupt the user per minute.

    The unit of the budget is an interruption: one moment where the
    agent demands that the human look at it. Working is free; asking
    is not.

    Zero is the safe default - the agent may not interrupt at all, it
    only reports and waits. A positive number allows that many
    interruptions inside a sliding window of one minute; the oldest
    one falls out of the window and the allowance comes back.

    Every attempt is counted, the refused ones included. An
    interruption stopped by the budget is exactly the interruption the
    user did not have to suffer, so it is counted too.
    """

    def __init__(self, per_minute: int = 0,
                 clock: Callable[[], float] = time.monotonic,
                 window_s: float = 60.0) -> None:
        self.per_minute = max(0, int(per_minute))
        self._clock = clock
        self._window_s = window_s
        self._stamps: list[float] = []
        #: how many interruptions were refused - the number a human
        #: would have been annoyed by, and did not have to be.
        #: ``disturbance`` is the narrower reading of it.
        self.refused = 0

    def _prune(self) -> None:
        cutoff = self._clock() - self._window_s
        self._stamps = [t for t in self._stamps if t > cutoff]

    @property
    def used(self) -> int:
        """Interruptions still inside the window."""
        self._prune()
        return len(self._stamps)

    @property
    def disturbance(self) -> int:
        """How much of the user's attention this budget protected.

        This is the number the isolation layer reads: the interruptions
        the user did not have to suffer. It equals ``refused`` whenever
        a budget was actually granted, and it is zero when no budget
        was granted at all - a budget of zero was never exhausted, so
        its refusals say the agent is misconfigured, not that the
        desktop is too small for the work.

        It never counts a request that was never made: ``take(False)``
        leaves both counters where they were.
        """
        return self.refused if self.per_minute > 0 else 0

    @property
    def exhausted(self) -> bool:
        """True when there was a budget and it is used up.

        A budget of zero was never given, so it is not "exhausted" -
        the same reading ``FocusBudget`` gives it. ``allows`` is the
        gate in either case.
        """
        return self.per_minute > 0 and self.used >= self.per_minute

    def allows(self) -> bool:
        """May one more interruption happen now?"""
        if self.per_minute <= 0:
            return False
        self._prune()
        return len(self._stamps) < self.per_minute

    def spend(self) -> bool:
        """Record one interruption. False when the budget says no."""
        if not self.allows():
            self.refused += 1
            return False
        self._stamps.append(self._clock())
        return True

    def take(self, needs_attention: bool) -> bool:
        """Interrupt the user - but only if the budget allows it.

        ``needs_attention=False`` means the agent has nothing to say to
        the human, so it must not reach for the cursor, and it must not
        spend the budget either: it did not interrupt anybody. It is
        not a refusal either, so it does not land in ``refused``: a
        request that was never made is no evidence of any disturbance.

        ``needs_attention=True`` spends one interruption when the
        budget allows it. When it does not, the attempt is refused and
        counted - the agent goes and queues instead of interrupting.
        """
        if not needs_attention:
            return False
        return self.spend()

    def to_dict(self) -> dict[str, Any]:
        return {
            "per_minute": self.per_minute,
            "used": self.used,
            "refused": self.refused,
            "exhausted": self.exhausted,
        }
