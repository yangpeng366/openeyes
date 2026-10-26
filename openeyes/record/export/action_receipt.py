"""Export a trajectory as ai-action-trace-receipt-standard YAML.

Output schema:
- one Action Trace Receipt (1 per trajectory)
- N UI Action Events (one per step)

We emit text-only fingerprint for state_before / state_after (window_title +
app_name + hwnd hash) instead of screenshot_hash. This keeps every receipt
fully textual, which is the whole point of "no vision required".
"""
from __future__ import annotations

import hashlib
import time
import uuid

from ..event import InputEvent, Trajectory, UIAEvent


def _state_fingerprint(app_name: str, window_title: str, hwnd: int) -> str:
    raw = f"{app_name}|{window_title}|{hwnd}".encode()
    return "sha256:" + hashlib.sha256(raw).hexdigest()[:16]


def _label_for(step) -> str:
    if isinstance(step, UIAEvent):
        ed = step.element
        name = (ed.get("name") or "").strip()
        ctype = (ed.get("control_type") or "").strip()
        if step.action == "type":
            return f'Type "{step.input_text or ""}" into {ctype} "{name}"'
        return f'{step.action.capitalize()} on {ctype} "{name}"'
    if isinstance(step, InputEvent):
        return f"Hotkey {step.combo}" if step.action == "hotkey" else step.action
    return step.action


def to_action_receipt(traj: Trajectory, *, human_prompt: str = "", surface: str = "openeyes-record-session") -> str:
    """Render an ai-action-trace-receipt YAML bundle for the trajectory."""
    receipt_id = f"atr-{uuid.uuid4().hex[:8]}"
    intent_hash = "sha256:" + hashlib.sha256((human_prompt or traj.name).encode("utf-8")).hexdigest()[:16]
    lines: list[str] = []
    # ---- Receipt ----
    lines.append("action_trace_receipt:")
    lines.append(f"  receipt_id: \"{receipt_id}\"")
    lines.append("  version: \"0.5.0\"")
    lines.append("  origin_intent:")
    lines.append(f"    human_prompt: \"{(human_prompt or traj.description or traj.name).replace(chr(34), chr(92)+chr(34))}\"")
    lines.append(f"    intent_hash: \"{intent_hash}\"")
    lines.append("  knowledge_context: {}")
    lines.append("  environment:")
    lines.append("    type: \"desktop\"")
    lines.append(f"    surface: \"{surface}\"")
    lines.append("    external_side_effect: false")
    lines.append("  state_before:")
    app0 = traj.steps[0].app_name if traj.steps and hasattr(traj.steps[0], "app_name") else traj.app_name
    title0 = traj.steps[0].window_title if traj.steps and hasattr(traj.steps[0], "window_title") else ""
    hwnd0 = traj.steps[0].hwnd if traj.steps and hasattr(traj.steps[0], "hwnd") else 0
    lines.append(f"    app_name: \"{app0}\"")
    lines.append(f"    window_title: \"{title0}\"")
    lines.append(f"    state_fingerprint: \"{_state_fingerprint(app0, title0, hwnd0)}\"")
    lines.append("    ui_elements: []")
    lines.append("  action:")
    lines.append("    type: \"replay\"")
    lines.append(f"    target: \"Recorded workflow '{traj.name}'\"")
    lines.append(f"    intent: \"Replay {len(traj.steps)} recorded actions deterministically.\"")
    lines.append("  consent_gate:")
    lines.append("    required: false")
    lines.append("    approved: true")
    lines.append("  state_after: {}")
    lines.append("  risk:")
    lines.append("    level: \"medium\"")
    lines.append("    reversible: true")
    lines.append("  trace_links: {}")
    lines.append("  royalty_bridge: {}")
    lines.append("")
    # ---- UI Action Events ----
    for i, step in enumerate(traj.steps):
        ev_id = f"uae-{receipt_id[-8:]}-{i:03d}"
        lines.append("# ---")
        lines.append("ui_action_event:")
        lines.append(f"  event_id: \"{ev_id}\"")
        lines.append("  version: \"0.2.0\"")
        lines.append(f"  parent_receipt_id: \"{receipt_id}\"")
        lines.append(f"  sequence_index: {i}")
        lines.append(f"  timestamp: \"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(step.ts))}\"")
        lines.append("  actor:")
        lines.append("    type: \"ai_agent\"")
        lines.append("    id: \"openeyes-record\"")
        lines.append("    model: \"openeyes-replay-v1\"")
        lines.append("  environment:")
        lines.append("    type: \"desktop\"")
        if hasattr(step, "window_title"):
            lines.append(f"    surface: \"{step.window_title or traj.app_name or 'desktop'}\"")
        else:
            lines.append(f"    surface: \"{traj.app_name or 'desktop'}\"")
        lines.append("  event:")
        if isinstance(step, UIAEvent):
            kind = step.action
            if kind == "type":
                kind = "type"
            lines.append(f"    type: \"{kind}\"")
            intent = step.intent or _label_for(step)
        else:
            lines.append(f"    type: \"{step.action}\"")
            intent = step.intent or _label_for(step)
        lines.append(f"    intent: \"{intent.replace(chr(34), chr(92)+chr(34))}\"")
        lines.append("  target:")
        if isinstance(step, UIAEvent):
            ed = step.element
            name = (ed.get("name") or "").strip()
            aid = (ed.get("automation_id") or "").strip()
            lines.append(f"    label: \"{name.replace(chr(34), chr(92)+chr(34))}\"")
            sel = step.selector or ""
            if sel.startswith("automation_id="):
                lines.append(f"    selector: \"{aid}\"")
            elif sel.startswith("name="):
                lines.append(f"    selector: \"name={name}\"")
            else:
                lines.append(f"    selector: \"{sel}\"")
            center = ed.get("center") or {}
            lines.append("    coordinates:")
            lines.append(f"      x: {int(center.get('x', 0))}")
            lines.append(f"      y: {int(center.get('y', 0))}")
        else:
            lines.append(f"    label: \"{_label_for(step)}\"")
            if step.action == "hotkey":
                lines.append(f"    selector: \"{step.combo}\"")
            else:
                lines.append("    selector: \"\"")
            lines.append("    coordinates: {x: 0, y: 0}")
        if isinstance(step, UIAEvent) and step.action == "type":
            lines.append("  input:")
            text = step.input_text or ""
            lines.append(f"    text: \"{text.replace(chr(34), chr(92)+chr(34))}\"")
            lines.append("    redacted: false")
        # State fingerprint for this step
        app = getattr(step, "app_name", traj.app_name)
        title = getattr(step, "window_title", "")
        hwnd = getattr(step, "hwnd", 0)
        lines.append("  state:")
        lines.append(f"    before_hash: \"{_state_fingerprint(app, title, hwnd)}\"")
        lines.append(f"    after_hash: \"{_state_fingerprint(app, title, hwnd)}\"")
        lines.append("  consent:")
        lines.append("    required: false")
        lines.append("    approved: true")
        lines.append("  result:")
        lines.append("    status: \"pending\"")
        lines.append(f"    summary: \"Recorded step '{_label_for(step)}'.\"")
        lines.append("  risk:")
        lines.append("    level: \"low\"")
        lines.append("    reversible: true")
        lines.append("")
    return "\n".join(lines)