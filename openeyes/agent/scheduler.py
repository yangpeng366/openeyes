"""Who gets the target first, when several agents want the same one.

A mouse cluster has agents that want the same window, the same
automation, the same button. Two of them acting on it at once produce
garbage: two cursors, one click each, and a user who has no idea
which agent moved.

The scheduler decides the order and keeps the receipts. It answers one
question - "may I have this target, and who had it before me" - and it
answers it the same way every time: the holder keeps the target unless
the newcomer has a strictly higher priority, in which case the
holder is pushed aside. Equal priority does not preempt, so two agents
of the same rank never take turns stealing from each other.

Every clash is recorded, preemption included: a preemption is still a
conflict, because somebody was interrupted. The loser is not queued
here - the queue belongs to the caller, which knows what the agents
are waiting for; the scheduler only says who lost and what they lost
over.

None of that is disturbance of the human. A clash is between two
agents and the user sees nothing. The scheduler is in fact how a
shared desktop absorbs contention: it turns work that wanted to be
parallel into work that is serial. So a conflict count must never be
read as "the disturbance budget was exhausted"; what justifies
leaving the desktop is the *work* needing two agents at the same
time, not the scheduler reporting a clash.

This is deliberately not the lease. The lease in
``openeyes.agent.lease`` guards the physical input of one desktop, and
it holds across processes. The scheduler orders the *intentions* within
one process: who works on which target, and in what order. They do not
overlap, and neither of them touches the desktop.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class Conflict:
    """Two agents wanted the same target.

    ``agent_a`` is the one that held the target first, ``agent_b`` the
    one that wanted it afterwards. A preemption is recorded the same
    way: the pushed-aside agent is ``agent_a``.
    """

    agent_a: str
    agent_b: str
    target: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_a": self.agent_a,
            "agent_b": self.agent_b,
            "target": self.target,
        }


class Scheduler:
    """The order of the agents, and who holds what.

    A target is any string key - an automation id, ``"hwnd:123"``, a
    window name. Same key, same target, hence same contention.

    The state is small on purpose: who holds each target, at what
    priority, and every clash that happened. The scheduler is inert;
    it grants and records, and it never acts on a desktop.
    """

    def __init__(self) -> None:
        #: target -> the agent holding it
        self._holders: dict[str, str] = {}
        #: target -> the priority the holder claimed it with
        self._priorities: dict[str, int] = {}
        #: agent -> the targets it holds
        self._agent_targets: dict[str, set[str]] = {}
        self._conflicts: list[Conflict] = []

    def _hold(self, agent_id: str, target: str, priority: int) -> None:
        """Record that an agent holds a target, dropping any former holder."""
        previous = self._holders.get(target)
        if previous is not None and previous != agent_id:
            held = self._agent_targets.get(previous)
            if held is not None:
                held.discard(target)
                if not held:
                    self._agent_targets.pop(previous, None)
        self._holders[target] = agent_id
        self._priorities[target] = priority
        self._agent_targets.setdefault(agent_id, set()).add(target)

    def claim(self, agent_id: str, target: str, priority: int = 0) -> bool:
        """Ask for a target. False means somebody else keeps it.

        A free target is granted and remembered. A target held by
        somebody else is kept by them, unless this agent has a
        strictly higher priority - then the target is taken over and
        the clash is recorded as a conflict.

        Re-claiming a target one already holds succeeds, and states
        the priority again.
        """
        agent_id = str(agent_id)
        target = str(target)
        if not agent_id:
            raise ValueError("an agent must have an id")
        if not target:
            raise ValueError("a target must have a name")
        priority = int(priority)

        holder = self._holders.get(target)
        if holder is None:
            self._hold(agent_id, target, priority)
            return True
        if holder == agent_id:
            self._priorities[target] = priority
            return True
        if priority > self._priorities.get(target, 0):
            self._hold(agent_id, target, priority)
            self._conflicts.append(Conflict(agent_a=holder, agent_b=agent_id,
                                            target=target))
            return True
        return False

    def release(self, agent_id: str) -> None:
        """Give back every target an agent holds. Unknown agents are fine."""
        agent_id = str(agent_id)
        targets = self._agent_targets.pop(agent_id, None)
        if not targets:
            return
        for target in targets:
            if self._holders.get(target) == agent_id:
                del self._holders[target]
                self._priorities.pop(target, None)

    def holder(self, target: str) -> str | None:
        """Who holds a target right now, if anybody."""
        return self._holders.get(str(target))

    def would_conflict(self, agent_id: str, target: str) -> bool:
        """Would claiming this target clash with somebody else?

        Read-only: it changes nothing. It answers the question before
        the fact, so an agent can queue instead of colliding.

        A preemption counts as a clash too: the target would be taken
        from its holder either way, and somebody is pushed aside.
        """
        holder = self._holders.get(str(target))
        if holder is None:
            return False
        return holder != str(agent_id)

    def conflicts(self) -> list[Conflict]:
        """Every clash so far, oldest first."""
        return list(self._conflicts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "claims": dict(self._holders),
            "conflicts": [conflict.to_dict() for conflict in self._conflicts],
        }
