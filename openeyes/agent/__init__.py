"""The agent layer: identity, orchestration and accounting for many agents.

Nothing in here is required to drive a desktop. A caller that runs a
single agent never imports this package; it exists so that a caller who
*does* run several agents at once has a place to put their identity,
their ordering rules and their receipts.

The layer is deliberately inert: every part of it records or schedules,
and none of it reaches for the desktop on its own. Actuation stays where
it already is - in the backends. The newer parts are inert in the same
way: the attention budget only counts, the scheduler only orders, the
isolation verdict only concludes, and the presence only draws a picture.
None of them decides anything for the human.
"""

from openeyes.agent.attention import AttentionBudget
from openeyes.agent.errors import (
    BACKGROUND_UNAVAILABLE,
    BUDGET_EXCEEDED,
    LEASE_TIMEOUT,
    NO_SEMANTIC_PATH,
    TARGET_CONFLICT,
)
from openeyes.agent.isolation import (
    ALL_LEVELS,
    LEVEL_NONE,
    LEVEL_SESSION,
    LEVEL_VM,
    IsolationConfig,
    IsolationVerdict,
    evaluate_isolation,
)
from openeyes.agent.lease import InteractionLease, LeaseTimeout
from openeyes.agent.ledger import ActionLedger, LedgerEntry, record_action
from openeyes.agent.overlay import CursorOverlay, render_cursor
from openeyes.agent.presence import AgentPresence
from openeyes.agent.profile import (
    MODE_ACT,
    MODE_OBSERVE,
    MODE_SHADOW,
    AgentProfile,
    FocusBudget,
    color_for,
)
from openeyes.agent.scheduler import Conflict, Scheduler

__all__ = [
    "ALL_LEVELS",
    "ActionLedger",
    "AgentPresence",
    "AgentProfile",
    "AttentionBudget",
    "BACKGROUND_UNAVAILABLE",
    "BUDGET_EXCEEDED",
    "Conflict",
    "CursorOverlay",
    "FocusBudget",
    "InteractionLease",
    "IsolationConfig",
    "IsolationVerdict",
    "LEASE_TIMEOUT",
    "LEVEL_NONE",
    "LEVEL_SESSION",
    "LEVEL_VM",
    "LeaseTimeout",
    "LedgerEntry",
    "MODE_ACT",
    "MODE_OBSERVE",
    "MODE_SHADOW",
    "NO_SEMANTIC_PATH",
    "Scheduler",
    "TARGET_CONFLICT",
    "color_for",
    "evaluate_isolation",
    "record_action",
    "render_cursor",
]