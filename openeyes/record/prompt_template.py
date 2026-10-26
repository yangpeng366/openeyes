"""Prompt templates for the prompt-based generator.

Two layers:
1. SYSTEM — instructs the model that every step must be (element, action)
   structured data. Refuses to emit vision-dependent actions.
2. USER — given a workflow description and the Element schema for the
   target app, ask the model to emit a Trajectory JSONL.

The model is told it can fall back to existing trajectory examples in the
workspace (few-shot), or invent reasonable steps if no examples match.
"""
from __future__ import annotations

from typing import Any

SYSTEM_PROMPT = """You are OpenEyes Record's trajectory generator.

Goal: given a workflow description in natural language and the available
UIA element schema for the target app, emit ONE Trajectory JSONL that
replays the workflow deterministically using only structured (element,
action) pairs. NO screenshots, NO vision model, NO pixel coordinates.

Every step must be one of:
- click: on a specific UIA element (Button, Edit, ListItem, ...). Use the
  element's automation_id when available; else name + control_type.
- type: text into a specific Edit element.
- hotkey: a key combo like "ctrl+s" or "Enter".
- wait: a small delay in ms (default 500) when state must settle.
- scroll: with dx / dy in element-relative units.

Use ONLY the Element schema below. Each step must include:
- action (string)
- element: {control_type, name, automation_id, class_name, hint, state}
- selector: "automation_id=<id>" OR "name=<n>|type=<t>" OR "hint=<h>|type=<t>"
- intent: a short natural-language description of WHY this step exists
- input_text: only for action="type"

Output strictly one JSONL: first line is the header, then one JSON per step.
No commentary, no markdown fence.
"""

USER_PROMPT_TEMPLATE = """# Workflow description
{description}

# Target app
{app_name}

# Element schema available (name -> automation_id, control_type, hint)
{element_inventory}

# Optional few-shot trajectories from past recordings
{few_shot_examples}

# Now emit:
- One header line: {{"_kind":"header","id":"...","name":"...","started_at":<float>,"app_name":"{app_name}","description":"...","step_count":<int>}}
- Then N step lines, each exactly the shape described in the system prompt.

Emit ONLY the JSONL. No prose around it.
"""


def render_system() -> str:
    return SYSTEM_PROMPT


def render_user(
    description: str,
    app_name: str,
    element_inventory: list[dict[str, Any]],
    few_shot_examples: list[str] | None = None,
) -> str:
    inv_lines = []
    for el in element_inventory:
        inv_lines.append(
            f"- name={el.get('name', '')!r} "
            f"control_type={el.get('control_type', '')!r} "
            f"automation_id={el.get('automation_id', '')!r} "
            f"hint={el.get('hint', '') or ''}"
        )
    inv = "\n".join(inv_lines) if inv_lines else "(no elements discovered yet; assume reasonable defaults)"
    few_shot = "\n\n---\n\n".join(few_shot_examples) if few_shot_examples else "(no prior recordings)"
    return USER_PROMPT_TEMPLATE.format(
        description=description,
        app_name=app_name,
        element_inventory=inv,
        few_shot_examples=few_shot,
    )