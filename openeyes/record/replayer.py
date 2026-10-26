"""Replayer: turn a Trajectory back into OpenEyes actuate() calls.

Strategy:
- For each step, derive a selector (built at capture time) and feed it to
  OpenEyes's click_by_selector / send_hotkey / type_text.
- For selectors without a structured key, fall back to bbox center click.
- `dry_run=True` prints every planned actuate() call without sending real
  input - safe to run on any machine.

This works on text-only LLMs: the trajectory JSONL is fully readable, and
the replayer does its job with OpenEyes structured primitives only - no
pixels, no screenshots, no vision model.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from .event import InputEvent, Trajectory, UIAEvent


@dataclass
class ReplayReport:
    trajectory_id: str
    trajectory_name: str
    dry_run: bool
    actions: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "trajectory_id": self.trajectory_id,
            "trajectory_name": self.trajectory_name,
            "dry_run": self.dry_run,
            "actions": self.actions,
            "skipped_count": len(self.skipped),
            "errors": self.errors,
        }


def _describe_actuate(step) -> dict[str, Any] | None:
    """Translate one step into an actuate() plan dict."""
    if isinstance(step, InputEvent):
        if step.action == "hotkey":
            return {"kind": "hotkey", "combo": step.combo, "intent": step.intent}
        if step.action == "wait":
            return {"kind": "wait", "ms": step.duration_ms or 500, "intent": step.intent}
        return None

    if isinstance(step, UIAEvent):
        ed = step.element
        if step.action == "click" or step.action.endswith("_click"):
            return {
                "kind": "click",
                "selector": step.selector,
                "element": ed,
                "intent": step.intent,
                "meta": step.meta,
            }
        if step.action == "type":
            return {
                "kind": "type",
                "selector": step.selector,
                "element": ed,
                "text": step.input_text,
                "intent": step.intent,
            }
        if step.action == "scroll":
            return {"kind": "scroll", "dx": step.meta.get("dx", 0), "dy": step.meta.get("dy", 0)}
        if step.action == "drag":
            return {"kind": "drag",
                    "from_selector": step.selector,
                    "to": step.meta.get("to"),
                    "intent": step.intent}
    return None


class Replayer:
    """Replay a Trajectory by emitting OpenEyes actuate() calls.

    When `dry_run=True` (default), no real input is sent; only the plan is
    returned in the report. Set `dry_run=False` to actually actuate.
    """

    def __init__(self, dry_run: bool = True, fail_on_unknown: bool = False):
        self.dry_run = dry_run
        self.fail_on_unknown = fail_on_unknown

    def replay(self, trajectory: Trajectory) -> ReplayReport:
        report = ReplayReport(
            trajectory_id=trajectory.id,
            trajectory_name=trajectory.name,
            dry_run=self.dry_run,
        )

        # Import inside the method so the package is importable even if
        # openeyes is not yet installed (e.g. unit tests).
        try:
            import openeyes  # noqa: F401
        except Exception as e:  # pragma: no cover
            report.errors.append(f"openeyes not importable: {e}")
            if self.fail_on_unknown:
                return report
            # In dry-run we still build the plan even without openeyes.

        for i, step in enumerate(trajectory.steps):
            plan = _describe_actuate(step)
            if plan is None:
                report.skipped.append({"index": i, "reason": "unknown action", "step": step.to_dict()})
                continue

            if self.dry_run:
                report.actions.append({"index": i, "plan": plan})
                continue

            try:
                self._dispatch(plan)
                report.actions.append({"index": i, "plan": plan, "sent": True})
            except Exception as e:
                report.errors.append(f"step {i}: {e}")

        return report

    def _dispatch(self, plan: dict[str, Any]) -> None:
        """Send one actuate() call to OpenEyes (real input)."""
        import openeyes
        kind = plan["kind"]
        if kind == "hotkey":
            openeyes.send_hotkey(plan["combo"], dry_run=False)
        elif kind == "wait":
            time.sleep((plan["ms"] or 500) / 1000.0)
        elif kind == "click":
            ed = plan["element"]
            # Use selector if structured, else fallback to center.
            selector = plan["selector"] or ""
            if selector.startswith("automation_id=") or selector.startswith("name="):
                # OpenEyes understands "automation_id=<id>" and "name=<n>"
                # We feed the full selector string verbatim.
                openeyes.click_by_selector(
                    hwnd=ed.get("_hwnd", 0),
                    selector=selector,
                    dry_run=False,
                )
            else:
                center = ed.get("center") or {}
                openeyes.click_xy(int(center.get("x", 0)), int(center.get("y", 0)), dry_run=False)
        elif kind == "type":
            ed = plan["element"]
            # Click the element first to focus, then type.
            openeyes.click_by_selector(
                hwnd=ed.get("_hwnd", 0),
                selector=plan["selector"],
                dry_run=False,
            )
            openeyes.type_text(plan["text"] or "", dry_run=False)
        elif kind == "scroll":
            openeyes.scroll(int(plan.get("dx", 0)), int(plan.get("dy", 0)), dry_run=False)
        elif kind == "drag":
            # drag(from_selector, to_xy, dry_run=False)
            ed_from = plan.get("element") or {}
            to = plan.get("to") or {}
            from_xy = (ed_from.get("center") or {}).get("x"), (ed_from.get("center") or {}).get("y")
            openeyes.drag(from_xy, (to.get("x"), to.get("y")), dry_run=False)