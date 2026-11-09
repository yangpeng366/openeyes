"""The agent layer: identity, orchestration and accounting for many agents.

Nothing in here is required to drive a desktop. A caller that runs a
single agent never imports this package; it exists so that a caller who
*does* run several agents at once has a place to put their identity,
their ordering rules and their receipts.

The layer is deliberately inert: every part of it records or schedules,
and none of it reaches for the desktop on its own. Actuation stays where
it already is - in the backends.
"""

from openeyes.agent.errors import (
    BACKGROUND_UNAVAILABLE,
    BUDGET_EXCEEDED,
    LEASE_TIMEOUT,
    NO_SEMANTIC_PATH,
    TARGET_CONFLICT,
)
from openeyes.agent.ledger import ActionLedger, LedgerEntry, record_action
from openeyes.agent.profile import (
    MODE_ACT,
    MODE_OBSERVE,
    MODE_SHADOW,
    AgentProfile,
    FocusBudget,
    color_for,
)

__all__ = [
    "ActionLedger",
    "AgentProfile",
    "BACKGROUND_UNAVAILABLE",
    "BUDGET_EXCEEDED",
    "FocusBudget",
    "LEASE_TIMEOUT",
    "LedgerEntry",
    "MODE_ACT",
    "MODE_OBSERVE",
    "MODE_SHADOW",
    "NO_SEMANTIC_PATH",
    "TARGET_CONFLICT",
    "color_for",
    "record_action",
]