"""Real OpenEyes demo: list live windows, detect elements on the AnyVPN / Feishu
window, and build a trajectory from a *recorded* user script.

This script does NOT drive the real UI - it only reads structured data.
It exists so we can verify that openeyes_record produces a valid trajectory
JSONL from real UIA / CDP data.

Run:
    python examples/demo_real.py [window_substring]
        e.g. python examples/demo_real.py Feishu
             python examples/demo_real.py AnyVPN
"""
import os, sys, json
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from openeyes.record import Recorder, Replayer, Trajectory
from openeyes.record.export import (
    to_codex_skill, to_action_receipt, to_opencua_trajectory,
)

try:
    import openeyes
except ImportError:
    print("ERROR: openeyes is not installed. Try: pip install -e E:\\gitAll\\openeyes",
          file=sys.stderr)
    sys.exit(1)


def main(window_substring: str = ""):
    out_dir = HERE / "out"
    out_dir.mkdir(exist_ok=True)

    print(f"Listing live windows containing: {window_substring!r}")
    wins = openeyes.list_windows()
    matches = [w for w in wins if not window_substring or
               window_substring.lower() in (w.title or "").lower()]
    print(f"  found {len(watches := matches)} match(es)")
    for w in matches[:10]:
        print(f"    hwnd={w.hwnd:>8}  title={w.title!r}  class={w.class_name!r}")

    if not matches:
        print("No matching windows. Pass a substring of an app that is open,")
        print("or open Feishu / AnyVPN / Edge and retry.")
        return

    # Use the first match.
    w = matches[0]
    print(f"\nUsing hwnd={w.hwnd} ({w.title!r})")
    elems = openeyes.detect_elements(w.hwnd, restore=True)
    print(f"  detected {len(elems)} interactive elements")
    for e in elems[:20]:
        print(f"    [{e.hint or '-':>2}] {e.control_type:<14} "
              f"name={e.name!r:<30}  aid={e.automation_id!r}")

    # Pick a small interesting subset and record a fake workflow.
    rec = Recorder()
    rec.start(name=f"explore-{w.title[:20]}", app_name=w.class_name or "unknown",
              description=f"Auto-exploration of {w.title}")

    # We do not actually click; we record the *intent* to click each
    # detectable element. This demonstrates the recorder pipeline on real
    # UIA data without modifying the live UI.
    target = [e for e in elems if e.interactive][:5]
    for e in target:
        rec.record_click(
            hwnd=w.hwnd,
            element=e,
            intent=f"Click {e.control_type} '{e.name}' (automation_id={e.automation_id})",
            window_title=w.title,
            pid=w.pid,
        )

    traj = rec.stop()
    jsonl_path = out_dir / "trajectory_real.jsonl"
    traj.save(str(jsonl_path))
    print(f"\nWrote {jsonl_path} ({len(traj.steps)} steps)")

    # Round-trip
    loaded = Trajectory.load(str(jsonl_path))
    assert loaded.id == traj.id

    # Dry-run replay
    report = Replayer(dry_run=True).replay(loaded)
    plan_path = out_dir / "replay_plan_real.json"
    plan_path.write_text(json.dumps(report.to_dict(), indent=2, ensure_ascii=False),
                         encoding="utf-8")
    print(f"Wrote {plan_path} (dry_run={report.dry_run}, actions={len(report.actions)})")

    # Export to three formats.
    codex = to_codex_skill(loaded,
        description=f"Replay an exploration of '{w.title}' using the recorded element clicks. UIA-only; no vision.")
    (out_dir / "SKILL_real.md").write_text(codex, encoding="utf-8")

    yaml_doc = to_action_receipt(loaded, human_prompt=f"Walk through the visible controls of {w.title}.")
    (out_dir / "action_trace_real.yaml").write_text(yaml_doc, encoding="utf-8")

    jsonl_doc = to_opencua_trajectory(loaded)
    (out_dir / "opencua_real.jsonl").write_text(jsonl_doc, encoding="utf-8")

    print("\nDONE. Open examples/out/ to inspect the artefacts.")


if __name__ == "__main__":
    sub = sys.argv[1] if len(sys.argv) > 1 else ""
    main(sub)