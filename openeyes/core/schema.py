"""Typed dataclasses for AI-friendly element / window schema."""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass(frozen=True)
class BBox:
    x: int
    y: int
    w: int
    h: int

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Center:
    x: int
    y: int

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ElementState:
    enabled: bool = True
    visible: bool = True
    focused: bool = False
    selected: bool = False


@dataclass
class Element:
    """One interactive UI element. Backend-agnostic.

    Attributes:
        backend: backend that produced this element (uia / ax / atspi / vision).
        control_type: high-level type (Button / Hyperlink / Edit / ...).
        name: visible label (may be empty for unnamed controls).
        automation_id: stable identifier from the accessibility tree.
        class_name: window class name (Win32) / AX class (mac).
        bbox: bounding box in window-relative coords.
        center: click target (bbox center).
        score: confidence in [0, 1] (1.0 for structured backends).
        hint: assigned letter for keyboard activation (e.g. "J"); None if not assigned.
        interactive: True if this element accepts user input.
        state: enabled / visible / focused flags.
        parent_chain: list of ancestor control_types (root -> immediate parent).
    """
    backend: str
    control_type: str
    name: str
    bbox: BBox
    center: Center
    automation_id: str = ""
    class_name: str = ""
    score: float = 1.0
    hint: Optional[str] = None
    interactive: bool = True
    state: ElementState = field(default_factory=ElementState)
    parent_chain: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["bbox"] = self.bbox.to_dict()
        d["center"] = self.center.to_dict()
        return d


@dataclass
class WindowInfo:
    hwnd: int
    pid: int
    title: str
    class_name: str
    x: int
    y: int
    w: int
    h: int

    def to_dict(self) -> dict:
        return asdict(self)