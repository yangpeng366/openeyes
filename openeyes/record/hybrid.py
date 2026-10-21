"""Hybrid trajectory generation (path C): recorded + prompt.

Takes one or more recorded trajectories (path A output) and a natural
language description, then synthesizes an enhanced trajectory using an
LLM. Falls back to deterministic pass-through of the recorded steps when
no LLM is configured.

Reference: xlang-ai/PC-Agent-E (arxiv:2505.13909) - human trajectories
boosted by AI augmentation outperform the original model.
"""
from __future__ import annotations
import time
from typing import List, Optional, Dict, Any

from .event import Trajectory, UIAEvent, InputEvent
from .abstract import to_natural_language
from .generator import _call_llm, LLMUnavailable
from .prompt_template import SYSTEM_PROMPT


def hybrid_generate(
    recorded: Trajectory,
    description: str,
    *,
    few_shot: Optional[List[Trajectory]] = None,
    backend: str = "auto",
    model: Optional[str] = None,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
) -> Trajectory:
    """Generate an enhanced Trajectory from a recorded one + NL description.

    Args:
        recorded: A path-A (show) trajectory captured via Recorder.
        description: Natural language description of what the workflow does
            or how it should be modified.
        few_shot: Optional list of existing trajectories as context examples.
        backend: "auto" (LLM if configured, else pass-through),
                 "real" (force LLM), or "mock" (deterministic pass-through).
        model / base_url / api_key: LLM overrides (see generator.py).

    Returns:
        A new Trajectory with the recorded steps preserved and enriched
        intents (mock) or LLM-refined steps (real).
    """
    if backend == "mock":
        return _hybrid_mock(recorded, description)
    if backend in ("auto", "real"):
        try:
            return _hybrid_real(
                recorded, description,
                few_shot=few_shot,
                model=model, base_url=base_url, api_key=api_key,
            )
        except LLMUnavailable:
            if backend == "real":
                raise
            return _hybrid_mock(recorded, description)
    raise ValueError(f"Unknown backend: {backend!r}. Use auto/real/mock.")


def _hybrid_mock(recorded: Trajectory, description: str) -> Trajectory:
    """Deterministic fallback: clone recorded steps, update description."""
    enhanced = Trajectory.new(
        name=recorded.name,
        app_name=recorded.app_name,
        description=description,
    )
    for step in recorded.steps:
        if isinstance(step, InputEvent):
            enhanced.add_input(step)
        else:
            enriched = UIAEvent(
                ts=step.ts,
                action=step.action,
                element=dict(step.element),
                selector=step.selector,
                app_name=step.app_name,
                window_title=step.window_title,
                hwnd=step.hwnd,
                pid=step.pid,
                intent=step.intent or description,
                input_text=step.input_text,
                meta=dict(step.meta),
            )
            enhanced.add(enriched)
    enhanced.close()
    return enhanced


def _hybrid_real(
    recorded: Trajectory,
    description: str,
    *,
    few_shot: Optional[List[Trajectory]] = None,
    model: Optional[str] = None,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
) -> Trajectory:
    """Call LLM with recorded trajectory NL + description -> refined steps."""
    recorded_nl = to_natural_language(recorded)

    few_shot_section = ""
    if few_shot:
        examples = []
        for fs in few_shot[:5]:
            examples.append(to_natural_language(fs))
        few_shot_section = "\n--- Few-shot examples ---\n" + "\n---\n".join(examples)

    user_prompt = (
        f"## Recorded trajectory\n{recorded_nl}\n"
        f"## User description\n{description}\n"
        f"{few_shot_section}\n"
        "Based on the recorded trajectory and the user's description, "
        "emit the refined Trajectory JSONL. Preserve the element schema "
        "(automation_id, control_type, name) exactly. Improve or add "
        "step intents for clarity. Output ONLY valid JSONL lines."
    )

    raw = _call_llm(
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        model=model,
        base_url=base_url,
        api_key=api_key,
    )
    return _parse_llm_trajectory(raw, recorded, description)


def _parse_llm_trajectory(
    raw: str,
    recorded: Trajectory,
    description: str,
) -> Trajectory:
    """Parse LLM JSONL output into a Trajectory, falling back to mock on error."""
    import json

    lines = [line.strip() for line in raw.strip().splitlines() if line.strip()]
    if not lines:
        return _hybrid_mock(recorded, description)

    steps: List[UIAEvent] = []
    for line in lines:
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        action = obj.get("action", "")
        element = obj.get("element", {})
        selector = obj.get("selector", "")
        if not action or not isinstance(element, dict):
            continue
        steps.append(UIAEvent(
            ts=time.time(),
            action=action,
            element=element,
            selector=selector,
            app_name=obj.get("app_name", recorded.app_name),
            intent=obj.get("intent", ""),
            input_text=obj.get("input_text"),
            meta={"source": "hybrid_llm"},
        ))

    if not steps:
        return _hybrid_mock(recorded, description)

    traj = Trajectory.new(
        name=recorded.name,
        app_name=recorded.app_name,
        description=description,
    )
    for step in steps:
        traj.add(step)
    traj.close()
    return traj