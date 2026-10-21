"""End-to-end demo using mock Element objects (no real Windows required).

Run:
    python examples/demo_mock.py

It will:
  1. Build a trajectory with 6 steps that rotates AnyVPN servers.
  2. Save it as JSONL.
  3. Dry-run the replay plan (no real input sent).
  4. Merge 3 hypothetical variants of the same workflow into a DAG.
  5. Export to Codex SKILL.md, ai-action-trace YAML, and OpenCUA JSONL.

All output goes to examples/out/.
"""
import os, sys, json
from pathlib import Path

# Make the package importable when running from the repo root
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from openeyes.record import (
    Recorder, Replayer, Trajectory,
    merge_trajectories, to_natural_language,
)
from openeyes.record.export import (
    to_codex_skill, to_action_receipt, to_opencua_trajectory,
)


class FakeElement:
    """Stand-in for openeyes.core.schema.Element.

    Mimics OpenEyes' to_dict() so we can exercise the recorder without a
    real UIA tree.
    """
    def __init__(self, control_type, name, automation_id="", class_name="",
                 bbox=(0, 0, 100, 30), hint=None, parent_chain=()):
        self.control_type = control_type
        self.name = name
        self.automation_id = automation_id
        self.class_name = class_name
        self.bbox = {"x": bbox[0], "y": bbox[1], "w": bbox[2], "h": bbox[3]}
        cx = bbox[0] + bbox[2] // 2
        cy = bbox[1] + bbox[3] // 2
        self.center = {"x": cx, "y": cy}
        self.hint = hint
        self.parent_chain = list(parent_chain)
        self.state = {"enabled": True, "visible": True, "focused": False, "selected": False}

    def to_dict(self):
        return {
            "backend": "uia",
            "control_type": self.control_type,
            "name": self.name,
            "automation_id": self.automation_id,
            "class_name": self.class_name,
            "bbox": self.bbox,
            "center": self.center,
            "score": 1.0,
            "hint": self.hint,
            "interactive": True,
            "state": self.state,
            "parent_chain": self.parent_chain,
        }


def build_anyvpn_rotation_trajectory() -> Trajectory:
    """Simulate the user clicking through AnyVPN to rotate servers."""
    rec = Recorder()
    rec.start(name="rotate-anyvpn-server", app_name="AnyVPN",
              description="Open AnyVPN -> click rotate -> wait -> confirm connected")

    hwnd = 862936  # example hwnd from openeyes 12-element AnyVPN enumeration

    # 1. Click the server list button
    rec.record_click(
        hwnd=hwnd,
        element=FakeElement("Button", "Select server", automation_id="ServerListToggle",
                            bbox=(120, 80, 160, 32), hint="a"),
        intent="Open the server list",
        window_title="AnyVPN",
    )

    # 2. Wait for the list to render
    rec.record_wait(duration_ms=400, intent="Let the list render")

    # 3. Type into the filter input
    rec.record_type(
        hwnd=hwnd,
        element=FakeElement("Edit", "Filter servers", automation_id="ServerFilter",
                            bbox=(80, 120, 240, 28), hint="f"),
        text="Hong Kong",
        intent="Filter to HK servers",
        window_title="AnyVPN",
    )

    # 4. Click the first HK result
    rec.record_click(
        hwnd=hwnd,
        element=FakeElement("ListItem", "Hong Kong - 03", automation_id="",
                            bbox=(60, 200, 280, 36), hint="s"),
        intent="Pick the third HK node",
        window_title="AnyVPN - server list",
    )

    # 5. Hotkey: confirm selection
    rec.record_hotkey(combo="ctrl+enter", intent="Apply the chosen server")

    # 6. Wait for connect
    rec.record_wait(duration_ms=1500, intent="Wait for connect to complete")

    # 7. Click the Secure my connection button
    rec.record_click(
        hwnd=hwnd,
        element=FakeElement("Button", "Secure my connection", automation_id="SecureButton",
                            bbox=(200, 60, 200, 40), hint="d"),
        intent="Activate VPN tunnel",
        window_title="AnyVPN",
    )

    return rec.stop()


def build_variant_trajectory_2() -> Trajectory:
    """Second run - same workflow with slightly different element names."""
    rec = Recorder()
    rec.start(name="rotate-anyvpn-server", app_name="AnyVPN",
              description="Variant: filter to Singapore instead")
    hwnd = 862936

    rec.record_click(
        hwnd=hwnd,
        element=FakeElement("Button", "Select server", automation_id="ServerListToggle",
                            bbox=(120, 80, 160, 32)),
        intent="Open the server list",
    )
    rec.record_type(
        hwnd=hwnd,
        element=FakeElement("Edit", "Filter servers", automation_id="ServerFilter",
                            bbox=(80, 120, 240, 28)),
        text="Singapore",
        intent="Filter to Singapore nodes",
    )
    rec.record_click(
        hwnd=hwnd,
        element=FakeElement("ListItem", "Singapore - 02", automation_id="",
                            bbox=(60, 240, 280, 36)),
        intent="Pick the second SG node",
    )
    rec.record_hotkey(combo="ctrl+enter", intent="Apply")
    rec.record_wait(duration_ms=1500)
    rec.record_click(
        hwnd=hwnd,
        element=FakeElement("Button", "Secure my connection", automation_id="SecureButton",
                            bbox=(200, 60, 200, 40)),
        intent="Activate VPN tunnel",
    )
    return rec.stop()


def build_variant_trajectory_3() -> Trajectory:
    """Third run - same workflow, user used Enter instead of ctrl+enter."""
    rec = Recorder()
    rec.start(name="rotate-anyvpn-server", app_name="AnyVPN")
    hwnd = 862936
    rec.record_click(
        hwnd=hwnd,
        element=FakeElement("Button", "Select server", automation_id="ServerListToggle",
                            bbox=(120, 80, 160, 32)),
        intent="Open the server list",
    )
    rec.record_type(
        hwnd=hwnd,
        element=FakeElement("Edit", "Filter servers", automation_id="ServerFilter",
                            bbox=(80, 120, 240, 28)),
        text="Tokyo",
        intent="Filter to Japan",
    )
    rec.record_click(
        hwnd=hwnd,
        element=FakeElement("ListItem", "Tokyo - 01", automation_id="",
                            bbox=(60, 280, 280, 36)),
        intent="Pick first JP node",
    )
    rec.record_hotkey(combo="Enter", intent="Apply")   # different combo
    rec.record_wait(duration_ms=1500)
    rec.record_click(
        hwnd=hwnd,
        element=FakeElement("Button", "Secure my connection", automation_id="SecureButton",
                            bbox=(200, 60, 200, 40)),
        intent="Activate VPN tunnel",
    )
    return rec.stop()


def main():
    out_dir = HERE / "out"
    out_dir.mkdir(exist_ok=True)

    print("=" * 70)
    print("Building trajectory #1 (AnyVPN rotate -> HK)")
    print("=" * 70)
    traj1 = build_anyvpn_rotation_trajectory()
    print(f"  steps: {len(traj1.steps)}")
    print(f"  id: {traj1.id}")

    # Save JSONL
    jsonl_path = out_dir / "trajectory.jsonl"
    traj1.save(str(jsonl_path))
    print(f"  wrote {jsonl_path}")

    # Round-trip
    loaded = Trajectory.load(str(jsonl_path))
    assert loaded.id == traj1.id
    assert len(loaded.steps) == len(traj1.steps)
    print(f"  round-trip OK ({len(loaded.steps)} steps)")

    print()
    print("=" * 70)
    print("Dry-run replay plan (no real input sent)")
    print("=" * 70)
    report = Replayer(dry_run=True).replay(traj1)
    print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False)[:2000])

    print()
    print("=" * 70)
    print("Natural-language description")
    print("=" * 70)
    print(to_natural_language(traj1))

    print()
    print("=" * 70)
    print("Build DAG from 3 variants of the same workflow")
    print("=" * 70)
    traj2 = build_variant_trajectory_2()
    traj3 = build_variant_trajectory_3()
    graph = merge_trajectories([traj1, traj2, traj3], workflow_name="rotate-anyvpn-server")
    print(f"  nodes: {len(graph.nodes)}")
    print(f"  edges: {len(graph.edges)}")
    for n in sorted(graph.nodes, key=lambda x: -x.count):
        print(f"  [{n.count}*] {n.label}")
    print()
    print("Top transitions:")
    for e in sorted(graph.edges, key=lambda x: -x.count)[:8]:
        f_label = next((n.label for n in graph.nodes if n.key == e.from_key), e.from_key)
        t_label = next((n.label for n in graph.nodes if n.key == e.to_key), e.to_key)
        print(f"  [{e.count}*] {f_label} -> {t_label}")

    dot_path = out_dir / "trajectory.dot"
    dot_path.write_text(graph.to_dot(), encoding="utf-8")
    print(f"  wrote {dot_path} (use `dot -Tpng {dot_path}` to render)")

    print()
    print("=" * 70)
    print("Export: Codex SKILL.md")
    print("=" * 70)
    codex = to_codex_skill(
        traj1,
        description="Replay the AnyVPN rotate-server workflow on the live AnyVPN window. All selectors come from UIA structure; no vision required.",
        model_hint="text-only LLM friendly (no vision required)",
    )
    codex_path = out_dir / "SKILL.md"
    codex_path.write_text(codex, encoding="utf-8")
    print(f"  wrote {codex_path} ({len(codex)} chars)")
    print(codex[:1500])
    print("...")

    print()
    print("=" * 70)
    print("Export: ai-action-trace-receipt YAML")
    print("=" * 70)
    yaml_doc = to_action_receipt(
        traj1,
        human_prompt="Rotate AnyVPN to a different node and reconnect.",
    )
    yaml_path = out_dir / "action_trace.yaml"
    yaml_path.write_text(yaml_doc, encoding="utf-8")
    print(f"  wrote {yaml_path} ({len(yaml_doc)} chars)")
    print(yaml_doc[:1500])
    print("...")

    print()
    print("=" * 70)
    print("Export: OpenCUA AgentNet-compatible JSONL")
    print("=" * 70)
    jsonl = to_opencua_trajectory(traj1)
    opencua_path = out_dir / "opencua.jsonl"
    opencua_path.write_text(jsonl, encoding="utf-8")
    print(f"  wrote {opencua_path} ({len(jsonl.splitlines())} lines)")
    print(jsonl[:1500])
    print("...")

    print()
    print("=" * 70)
    print("DONE - inspect examples/out/")
    print("=" * 70)
    for p in sorted(out_dir.iterdir()):
        print(f"  {p.name} ({p.stat().st_size} bytes)")


if __name__ == "__main__":
    main()