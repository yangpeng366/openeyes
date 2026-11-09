"""Output schema for the decision layer."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ActionKind(str, Enum):
    CLICK = "click"
    TYPE_TEXT = "type_text"
    KEY_PRESS = "key_press"
    SCROLL = "scroll"
    WAIT = "wait"
    DONE = "done"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class Action:
    """Backend-agnostic action.

    `target` is a reference into the caller-supplied Element list (by index),
    not a copy. Callers are responsible for resolving the target into a
    backend-specific call (e.g. `click_xy(el.center.x, el.center.y)`).

    `needs_attention` is the attention-budget signal: does this decision want
    a human to look at it? The decision layer raises it only for the
    FLAG_FOR_REVIEW band (the machine is not sure, a human should glance);
    every other band (ACT, FALLBACK, UNCERTAIN, and the blocked early
    returns) stays False because the machine handles those alone. Downstream
    (an agent spending a human's attention) consumes the flag; the decision
    layer never depends on the agent.
    """
    kind: ActionKind
    target_index: int | None = None
    text: str | None = None
    key: str | None = None
    dx: int | None = None
    dy: int | None = None
    reason: str = ""
    needs_attention: bool = False

    def to_dict(self) -> dict:
        return {
            "kind": self.kind.value,
            "target_index": self.target_index,
            "text": self.text,
            "key": self.key,
            "dx": self.dx,
            "dy": self.dy,
            "reason": self.reason,
            "needs_attention": self.needs_attention,
        }