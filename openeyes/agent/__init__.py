"""The agent layer: identity, orchestration and accounting for many agents.

Nothing in here is required to drive a desktop. A caller that runs a
single agent never imports this package; it exists so that a caller who
*does* run several agents at once has a place to put their identity,
their ordering rules and their receipts.

The layer is deliberately inert: every part of it records or schedules,
and none of it reaches for the desktop on its own. Actuation stays where
it already is - in the backends.
"""

from openeyes.agent.ledger import ActionLedger, LedgerEntry

__all__ = ["ActionLedger", "LedgerEntry"]