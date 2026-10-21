"""Output schema for the decision layer."""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Optional


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
    """
    kind: ActionKind
    target_index: Optional[int] = None
    text: Optional[str] = None
    key: Optional[str] = None
    dx: Optional[int] = None
    dy: Optional[int] = None
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "kind": self.kind.value,
            "target_index": self.target_index,
            "text": self.text,
            "key": self.key,
            "dx": self.dx,
            "dy": self.dy,
            "reason": self.reason,
        }