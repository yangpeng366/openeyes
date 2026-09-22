#!/usr/bin/env python3
"""Verify that UIA and Edge bridge recordings share the trajectory contract.

Run:
    python examples/verify_record_schema.py

The checker loads both files with Python, canonicalizes them through
``Trajectory.save``, reparses them with Node's bridge parser, and fails on a
header/action/schema mismatch. The structured result is useful as patrol
evidence and avoids another one-off verification script.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from openeyes.record import Trajectory


REQUIRED_UIA_KEYS = {
    "automation_id",
    "control_type",
    "name",
    "bbox",
    "center",
}


def _assert_uia_schema(trajectory: Trajectory, source: str) -> None:
    for index, step in enumerate(trajectory.steps):
        if not hasattr(step, "element"):
            continue
        missing = REQUIRED_UIA_KEYS - set(step.element)
        if missing:
            raise AssertionError(
                f"{source} step {index} missing UIA keys: {sorted(missing)}"
            )


def _node_parse(path: Path) -> dict[str, Any]:
    bridge_path = HERE / "lib" / "chrome-recorder-bridge.js"
    script = """
const fs = require('fs');
const { parseJSONL } = require(process.env.OPENEYES_VERIFY_BRIDGE);
const result = parseJSONL(fs.readFileSync(process.env.OPENEYES_VERIFY_PATH, 'utf8'));
console.log(JSON.stringify({
  step_count: result.header.step_count,
  actual: result.steps.filter((step) => !step._kind).length,
  actions: result.steps.map((step) => step.action),
}));
"""
    environment = os.environ.copy()
    environment["OPENEYES_VERIFY_BRIDGE"] = str(bridge_path)
    environment["OPENEYES_VERIFY_PATH"] = str(path)
    completed = subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
    )
    return json.loads(completed.stdout)


def _load(path: Path, source: str, *, require_uia_schema: bool = False) -> tuple[dict[str, Any], Trajectory]:
    trajectory = Trajectory.load(str(path))
    if require_uia_schema:
        _assert_uia_schema(trajectory, source)
    header = {
        "source": str(path),
        "name": trajectory.name,
        "step_count": len(trajectory.steps),
        "actions": [step.action for step in trajectory.steps],
        "selectors": [
            step.selector for step in trajectory.steps if hasattr(step, "selector")
        ],
    }
    return header, trajectory


def verify(
    uia_path: Path,
    edge_path: Path,
    report_path: Path,
    *,
    require_actions: bool = False,
) -> dict[str, Any]:
    uia_header, uia_trajectory = _load(uia_path, "uia", require_uia_schema=True)
    edge_header, edge_trajectory = _load(edge_path, "edge")

    with tempfile.TemporaryDirectory(prefix="openeyes-record-schema-") as directory:
        canonical_dir = Path(directory)
        canonical_uia = canonical_dir / "trajectory.uia.jsonl"
        canonical_edge = canonical_dir / "trajectory.edge.jsonl"
        uia_trajectory.save(str(canonical_uia))
        edge_trajectory.save(str(canonical_edge))

        report: dict[str, Any] = {
            "uia": {
                **uia_header,
                "canonical_node": _node_parse(canonical_uia),
                "original_node": _node_parse(uia_path),
            },
            "edge": {
                **edge_header,
                "canonical_node": _node_parse(canonical_edge),
                "original_node": _node_parse(edge_path),
            },
        }

    report["alignment"] = {
        "step_count_equal": uia_header["step_count"] == edge_header["step_count"],
        "action_sequence_equal": uia_header["actions"] == edge_header["actions"],
        "python_to_node": report["uia"]["canonical_node"]["step_count"]
        == uia_header["step_count"]
        and report["edge"]["canonical_node"]["step_count"] == edge_header["step_count"],
    }
    alignment = report["alignment"]
    action_matches = alignment["action_sequence_equal"]
    aligned = (
        alignment["step_count_equal"]
        and alignment["python_to_node"]
        and (action_matches or not require_actions)
    )

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if not aligned:
        raise AssertionError(
            f"trajectory alignment failed; report: {report_path}"
        )
    return report


def main() -> None:
    record_out = HERE / "record-out"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--uia",
        type=Path,
        default=record_out / "trajectory_real.jsonl",
        help="Path A/UIA trajectory JSONL",
    )
    parser.add_argument(
        "--edge",
        type=Path,
        default=record_out / "trajectory-edge-real.jsonl",
        help="Edge bridge trajectory JSONL",
    )
    parser.add_argument(
        "--require-actions",
        action="store_true",
        help="also require UIA and Edge action sequences to match",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=record_out / "record-schema-report.json",
        help="JSON evidence output path",
    )
    args = parser.parse_args()
    report = verify(
        args.uia.resolve(),
        args.edge.resolve(),
        args.report.resolve(),
        require_actions=args.require_actions,
    )
    print(json.dumps(report["alignment"], ensure_ascii=False, indent=2))
    print(f"Report: {args.report.resolve()}")


if __name__ == "__main__":
    main()
