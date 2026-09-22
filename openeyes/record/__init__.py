"""OpenEyes Record - structure-only recording and replay (no vision).

Every step is (element, action) where element is the OpenEyes Element schema
(name / automation_id / control_type / class_name / hint / parent_chain / state).
No screenshots, no pixels. Pure text LLMs can consume everything we emit.

Public API:
    recorder = Recorder(name="rotate-anyvpn-keepalive")
    recorder.start()
    recorder.record_click(window_hwnd, element)
    recorder.record_type(window_hwnd, element, "hello world")
    recorder.record_hotkey("ctrl+s")
    traj = recorder.stop()

    Replayer(dry_run=True).replay(traj)
    to_natural_language(traj)
    merge_trajectories([traj1, traj2, traj3])

See also:
    export.codex_skill.to_codex_skill
    export.action_receipt.to_action_receipt
    export.opencua.to_opencua_trajectory
"""
from .event import UIAEvent, InputEvent, Trajectory
from .recorder import Recorder
from .replayer import Replayer
from .abstract import merge_trajectories, to_natural_language, graph_to_natural_language
from .generator import generate_trajectory_from_prompt, LLMUnavailable
from .hybrid import hybrid_generate
from .prompt_template import render_system, render_user

__all__ = [
    "UIAEvent", "InputEvent", "Trajectory",
    "Recorder", "Replayer",
    "merge_trajectories", "to_natural_language", "graph_to_natural_language",
    "generate_trajectory_from_prompt", "LLMUnavailable", "hybrid_generate",
    "render_system", "render_user",
]