"""Recorder: capture OpenEyes actions into a Trajectory.

Usage A (declarative, simple):
    r = Recorder(name="rotate-anyvpn")
    r.start()
    r.record_click(hwnd, element)
    r.record_type(hwnd, element, "hello")
    r.record_hotkey("ctrl+s")
    traj = r.stop()

Usage B (capture from a real OS event hook, future):
    Provide your own producer that feeds UIAEvent / InputEvent objects via
    `recorder.feed(event)`.

The recorder does NOT capture pixels. Every step is a structured
(element, action) pair, which means the trajectory is fully text-LLM
readable and re-playable without any vision model.
"""
from __future__ import annotations

import time

from .event import InputEvent, Trajectory, UIAEvent


def _build_selector(element_dict: dict) -> str:
    """Pick a stable replay key.

    Priority: automation_id > name + control_type > hint + control_type.
    Fallback: bbox center so we can at least click the same pixel.
    """
    aid = (element_dict.get("automation_id") or "").strip()
    if aid:
        return f"automation_id={aid}"
    name = (element_dict.get("name") or "").strip()
    ctype = (element_dict.get("control_type") or "").strip()
    if name:
        return f"name={name}|type={ctype}"
    hint = (element_dict.get("hint") or "").strip()
    if hint:
        return f"hint={hint}|type={ctype}"
    center = element_dict.get("center") or {}
    cx = center.get("x")
    cy = center.get("y")
    return f"center=({cx},{cy})"


class Recorder:
    """Records OpenEyes actions into a Trajectory."""

    def __init__(self, name: str = "untitled", app_name: str = "", description: str = ""):
        self._trajectory: Trajectory | None = None

    def start(self, name: str = "untitled", app_name: str = "", description: str = "") -> Trajectory:
        if self._trajectory is not None:
            raise RuntimeError("Recorder already started; call stop() first.")
        self._trajectory = Trajectory.new(name=name, app_name=app_name, description=description)
        return self._trajectory

    @property
    def trajectory(self) -> Trajectory | None:
        return self._trajectory

    def stop(self) -> Trajectory:
        if self._trajectory is None:
            raise RuntimeError("Recorder not started.")
        self._trajectory.close()
        traj = self._trajectory
        self._trajectory = None
        return traj

    # ---- Declarative APIs (the simplest capture path) ----

    def record_click(
        self,
        hwnd: int,
        element,
        intent: str = "",
        button: str = "left",
        app_name: str = "",
        window_title: str = "",
        pid: int = 0,
        meta: dict | None = None,
    ) -> UIAEvent:
        """Record a click on a specific OpenEyes Element.

        `element` is an OpenEyes `openeyes.core.schema.Element` (or its
        to_dict()). We snapshot its dict NOW so later changes don't leak in.
        """
        if self._trajectory is None:
            raise RuntimeError("Recorder not started.")
        ed = element.to_dict() if hasattr(element, "to_dict") else dict(element)
        ev = UIAEvent(
            ts=time.time(),
            action="click" if button == "left" else f"{button}_click",
            element=ed,
            selector=_build_selector(ed),
            app_name=app_name or self._trajectory.app_name,
            window_title=window_title,
            hwnd=int(hwnd),
            pid=int(pid),
            intent=intent,
            meta={"button": button, **(meta or {})},
        )
        self._trajectory.add(ev)
        return ev

    def record_type(
        self,
        hwnd: int,
        element,
        text: str,
        intent: str = "",
        **kwargs,
    ) -> UIAEvent:
        if self._trajectory is None:
            raise RuntimeError("Recorder not started.")
        ed = element.to_dict() if hasattr(element, "to_dict") else dict(element)
        ev = UIAEvent(
            ts=time.time(),
            action="type",
            element=ed,
            selector=_build_selector(ed),
            hwnd=int(hwnd),
            intent=intent,
            input_text=text,
            meta=kwargs.pop("meta", {}) or {},
            **kwargs,
        )
        self._trajectory.add(ev)
        return ev

    def record_hotkey(self, combo: str, intent: str = "", **kwargs) -> InputEvent:
        if self._trajectory is None:
            raise RuntimeError("Recorder not started.")
        ev = InputEvent(
            ts=time.time(),
            action="hotkey",
            combo=combo,
            intent=intent,
            meta=kwargs.pop("meta", {}) or {},
        )
        self._trajectory.add(ev)
        return ev

    def record_wait(self, duration_ms: int = 500, intent: str = "") -> InputEvent:
        if self._trajectory is None:
            raise RuntimeError("Recorder not started.")
        ev = InputEvent(
            ts=time.time(),
            action="wait",
            duration_ms=duration_ms,
            intent=intent,
        )
        self._trajectory.add(ev)
        return ev

    # ---- Manual feed (for OS-level hooks) ----

    def feed(self, event) -> None:
        if self._trajectory is None:
            raise RuntimeError("Recorder not started.")
        self._trajectory.add(event)