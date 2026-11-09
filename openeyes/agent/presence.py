"""What one agent shows the human, and what that costs.

Two things decide whether an agent's cursor may move across the screen:

* whether the decision layer said a human should look - that is the
  ``needs_attention`` bit, and it is the only reason an agent has for
  reaching for the user's attention at all;
* whether the user can spare that attention - the attention budget.

Both must say yes. The first is a matter of principle: an agent that
has nothing to show must not move a cursor in front of somebody who is
trying to read. The second is a matter of arithmetic: the human's
attention is finite, and the budget says how much of it this minute is
available.

The glue is deliberately thin. It decides, it counts, it draws a
picture - and it never touches the pointer or the foreground window.
"""
from __future__ import annotations

from typing import Any

from openeyes.agent.attention import AttentionBudget
from openeyes.agent.overlay import CursorOverlay


class AgentPresence:
    """One agent's visible presence on a shared desktop."""

    def __init__(self, overlay: CursorOverlay, attention: AttentionBudget,
                 agent_id: str = "") -> None:
        self.overlay = overlay
        self.attention = attention
        self.agent_id = agent_id or overlay.agent_id
        #: is the cursor on the screen right now?
        self.shown = False
        #: how many times this agent wanted to be seen and was not allowed
        #: - the interruptions the user did not have to suffer
        self.refused = 0

    def announce(self, *, needs_attention: bool, x: int, y: int) -> bool:
        """Show this agent's cursor, if the human can spare the attention.

        False means the cursor stays off the screen: either the decision
        layer had nothing to show, or the attention budget said no.

        Only the second counts as a refusal. An agent with nothing to
        show did not reach for the user's attention, so nobody turned
        it down; counting that as a refusal would report quiet as
        interference. A refusal is an interruption the user did not
        have to suffer, and it is the honest measure of what the
        attention budget protected.
        """
        if not needs_attention:
            self.withdraw()
            return False
        if not self.attention.take(True):
            self.refused += 1
            self.withdraw()
            return False
        self.overlay.show(x, y)
        self.shown = bool(self.overlay.state.visible)
        return self.shown

    def withdraw(self) -> None:
        """Take the cursor back off the screen."""
        self.overlay.hide()
        self.shown = False

    def close(self) -> None:
        """Withdraw and destroy the window for good."""
        self.withdraw()
        self.overlay.close()

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "shown": self.shown,
            "refused": self.refused,
            "attention": self.attention.to_dict(),
        }
