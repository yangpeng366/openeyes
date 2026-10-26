"""Structured event / trajectory dataclasses.

Every recording event is one of:
- UIAEvent (click / type / hotkey / drag / scroll on a specific UIA element)
- InputEvent (raw keyboard combo without an element, e.g. global hotkey)

Both are JSON-serialisable via to_dict() so the trajectory is plain text.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class UIAEvent:
    """One click / type / drag / scroll on a specific UIA / CDP element.

    `element` is an OpenEyes Element.to_dict() snapshot at capture time.
    `selector` is a derived primary key for replay:
        Prefer `automation_id`; fall back to `name + control_type`.
    `window_state` is a structural fingerprint of the window at this moment:
        title + pid + hwnd. No pixels.
    """
    ts: float
    action: str           # "click" | "double_click" | "right_click" | "type" | "drag" | "scroll"
    element: dict[str, Any]   # OpenEyes Element.to_dict()
    selector: str        # "automation_id=<id>" or "name=<n>|type=<t>"
    app_name: str = ""
    window_title: str = ""
    hwnd: int = 0
    pid: int = 0
    intent: str = ""          # optional natural-language intent for this step
    input_text: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class InputEvent:
    """Raw input without an element (global hotkey, scroll wheel without target, etc.)."""
    ts: float
    action: str           # "hotkey" | "scroll" | "wait"
    combo: str | None = None       # e.g. "ctrl+s"
    dx: int | None = None
    dy: int | None = None
    duration_ms: int | None = None
    intent: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Trajectory:
    """A complete recording session: one workflow attempt, N steps.

    Stored as JSONL on disk (one JSON object per line: header + each event).
    """
    id: str
    name: str
    started_at: float
    ended_at: float | None = None
    app_name: str = ""
    description: str = ""
    steps: list[Any] = field(default_factory=list)   # list[UIAEvent | InputEvent]

    @staticmethod
    def new(name: str, app_name: str = "", description: str = "") -> Trajectory:
        return Trajectory(
            id=str(uuid.uuid4()),
            name=name,
            started_at=time.time(),
            app_name=app_name,
            description=description,
        )

    def add(self, event) -> None:
        self.steps.append(event)

    def close(self) -> None:
        if self.ended_at is None:
            self.ended_at = time.time()

    # ---- I/O ----

    def to_jsonl(self) -> str:
        header = {
            "_kind": "header",
            "id": self.id,
            "name": self.name,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "app_name": self.app_name,
            "description": self.description,
            "step_count": len(self.steps),
        }
        lines = [json.dumps(header, ensure_ascii=False)]
        for s in self.steps:
            lines.append(json.dumps(s.to_dict(), ensure_ascii=False))
        return "\n".join(lines) + "\n"

    @classmethod
    def from_jsonl(cls, text: str) -> Trajectory:
        lines = [ln for ln in text.splitlines() if ln.strip()]
        if not lines:
            raise ValueError("empty trajectory jsonl")
        header = json.loads(lines[0])
        if header.get("_kind") != "header":
            raise ValueError("first line must be header")
        traj = cls(
            id=header["id"],
            name=header["name"],
            started_at=header["started_at"],
            ended_at=header.get("ended_at"),
            app_name=header.get("app_name", ""),
            description=header.get("description", ""),
        )
        for ln in lines[1:]:
            obj = json.loads(ln)
            # reconstruct UIAEvent vs InputEvent by presence of "element"
            if "element" in obj:
                traj.steps.append(UIAEvent(**obj))
            else:
                traj.steps.append(InputEvent(**obj))
        return traj

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(self.to_jsonl())

    @classmethod
    def load(cls, path: str) -> Trajectory:
        with open(path, encoding="utf-8") as f:
            return cls.from_jsonl(f.read())

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "app_name": self.app_name,
            "description": self.description,
            "steps": [s.to_dict() for s in self.steps],
        }