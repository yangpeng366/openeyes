"""Prompt-based generation demo.

Shows the second pathway: instead of capturing a real workflow, the user
describes what they want and an LLM produces a structured trajectory.

Run modes:
    python examples/demo_prompt.py                 # mock backend (always works)
    LLM_API_KEY=sk-... python examples/demo_prompt.py --real   # real backend
"""
import os, sys, json
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from openeyes.record import (
    generate_trajectory_from_prompt,
    to_natural_language, merge_trajectories,
)
from openeyes.record.export import (
    to_codex_skill, to_action_receipt, to_opencua_trajectory,
)


def real_mode() -> bool:
    return "--real" in sys.argv


# A small "element inventory" we pretend came from running
#   openeyes.detect_elements(hwnd) on AnyAnyVPN.
ANYVPN_INVENTORY = [
    {"control_type": "Button",    "name": "Select server",        "automation_id": "ServerListToggle", "hint": "a"},
    {"control_type": "Edit",      "name": "Filter servers",       "automation_id": "ServerFilter",     "hint": "f"},
    {"control_type": "ListItem",  "name": "Hong Kong - 03",       "automation_id": "",                 "hint": "s"},
    {"control_type": "Button",    "name": "Secure my connection", "automation_id": "SecureButton",     "hint": "d"},
]

# Two natural-language workflow descriptions.
DESCRIPTIONS = [
    "Rotate AnyVPN to Hong Kong and reconnect.",
    "Search for 'openeyes' on the web and press Enter.",
]


def main():
    out_dir = HERE / "out"
    out_dir.mkdir(exist_ok=True)
    backend = "real" if real_mode() else "auto"

    print("=" * 70)
    print(f"Prompt-based generator (backend={backend})")
    print("=" * 70)

    trajs = []
    for desc in DESCRIPTIONS:
        print(f"\n>>> {desc}")
        try:
            traj = generate_trajectory_from_prompt(
                desc,
                app_name="AnyVPN",
                element_inventory=ANYVPN_INVENTORY,
                backend=backend,
            )
        except Exception as e:
            print(f"  ERROR: {e}")
            continue
        trajs.append(traj)
        print(f"  generated {len(traj.steps)} steps (id={traj.id[:8]})")
        print()
        print(to_natural_language(traj))

    if not trajs:
        print("No trajectories generated.")
        return

    # Merge into a DAG across both descriptions
    if len(trajs) >= 2:
        print("=" * 70)
        print("DAG across both generated trajectories")
        print("=" * 70)
        graph = merge_trajectories(trajs, workflow_name="prompt-generated")
        print(f"  nodes: {len(graph.nodes)}  edges: {len(graph.edges)}")
        for n in sorted(graph.nodes, key=lambda x: -x.count)[:8]:
            print(f"  [{n.count}*] {n.label}")

    # Export the first one in 3 formats
    t0 = trajs[0]
    (out_dir / "prompt_skill.md").write_text(to_codex_skill(t0, description=DESCRIPTIONS[0]),
                                              encoding="utf-8")
    (out_dir / "prompt_action_trace.yaml").write_text(to_action_receipt(t0, human_prompt=DESCRIPTIONS[0]),
                                                        encoding="utf-8")
    (out_dir / "prompt_opencua.jsonl").write_text(to_opencua_trajectory(t0), encoding="utf-8")
    print()
    print("=" * 70)
    print("Exports written to examples/out/")
    print("=" * 70)
    for f in ("prompt_skill.md", "prompt_action_trace.yaml", "prompt_opencua.jsonl"):
        p = out_dir / f
        print(f"  {f} ({p.stat().st_size} bytes)")


if __name__ == "__main__":
    main()