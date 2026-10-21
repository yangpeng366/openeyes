"""Export a trajectory as OpenCUA AgentNet-compatible JSONL.

Each step is one JSON object on its own line, with:
  - observation: structured element tree snapshot (no image)
  - thought: optional intent
  - action: pyautogui-style string OR structured selector action
"""
from __future__ import annotations
import json
import time
from typing import List, Dict, Any

from ..event import Trajectory, UIAEvent, InputEvent


def to_opencua_trajectory(traj: Trajectory) -> str:
    """Render a trajectory as AgentNet-style JSONL (one step per line)."""
    out: List[str] = []
    header = {
        "trajectory_id": traj.id,
        "workflow_name": traj.name,
        "app_name": traj.app_name,
        "description": traj.description,
        "started_at": traj.started_at,
        "ended_at": traj.ended_at,
        "step_count": len(traj.steps),
        "format": "opencua-agentnet-text-only-v1",
    }
    out.append(json.dumps(header, ensure_ascii=False))
    for i, step in enumerate(traj.steps):
        if isinstance(step, UIAEvent):
            ed = step.element
            obs = {
                "app_name": step.app_name or traj.app_name,
                "window_title": step.window_title,
                "hwnd": step.hwnd,
                "pid": step.pid,
                "target_element": ed,
                "selector": step.selector,
            }
            if step.action == "type":
                action = {"type": "type_text", "text": step.input_text or "", "selector": step.selector}
            elif step.action == "click" or step.action.endswith("_click"):
                button = step.meta.get("button", "left") if isinstance(step.meta, dict) else "left"
                action = {"type": "click", "selector": step.selector, "button": button,
                          "center": ed.get("center")}
            elif step.action == "scroll":
                action = {"type": "scroll", "dx": step.meta.get("dx", 0), "dy": step.meta.get("dy", 0)}
            elif step.action == "drag":
                action = {"type": "drag",
                          "from_selector": step.selector,
                          "to": step.meta.get("to")}
            else:
                action = {"type": step.action, "selector": step.selector}
            line_obj = {
                "step_index": i,
                "timestamp": step.ts,
                "observation": obs,
                "thought": step.intent or "",
                "action": action,
            }
        else:
            obs = {"app_name": traj.app_name}
            if step.action == "hotkey":
                action = {"type": "hotkey", "combo": step.combo}
            elif step.action == "wait":
                action = {"type": "wait", "ms": step.duration_ms or 500}
            else:
                action = {"type": step.action}
            line_obj = {
                "step_index": i,
                "timestamp": step.ts,
                "observation": obs,
                "thought": step.intent or "",
                "action": action,
            }
        out.append(json.dumps(line_obj, ensure_ascii=False))
    return "\n".join(out) + "\n"