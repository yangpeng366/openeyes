"""The ``record_replay`` MCP tool: dry by default, loud about failures.

The payload builder carries no MCP dependency, so it is tested directly; the
one test that goes through ``call_tool`` is skipped when the optional ``mcp``
package is not installed. Nothing here sends input: a live run is exercised
through a fake backend, never through the real desktop.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from openeyes.mcp.replay_tool import record_replay_payload
from openeyes.record import InputEvent, Trajectory, UIAEvent
from openeyes.record.replayer import Replayer

_ELEMENT = {
    "backend": "uia",
    "control_type": "Button",
    "name": "Submit",
    "bbox": {"x": 100, "y": 200, "w": 80, "h": 30},
    "center": {"x": 140, "y": 215},
    "automation_id": "btn_submit",
}


def _write_trajectory(tmp_path) -> tuple[str, Trajectory]:
    trajectory = Trajectory.new("mcp-live-replay")
    trajectory.add(InputEvent(ts=1.0, action="hotkey", combo="ctrl+l", duration_ms=0))
    trajectory.add(InputEvent(ts=2.0, action="wait", duration_ms=120))
    path = tmp_path / "trajectory.jsonl"
    trajectory.save(str(path))
    return str(path), trajectory


def _write_click_trajectory(tmp_path) -> tuple[str, Trajectory]:
    """Two clicks whose recorded expectation no backend ever observes."""
    trajectory = Trajectory.new("mcp-live-replay-failing")
    for ts in (1.0, 2.0):
        trajectory.add(UIAEvent(
            ts=ts, action="click", element=dict(_ELEMENT),
            selector="automation_id=btn_submit", hwnd=4242,
            meta={"expect": {"automation_id": "dialog_ok"}},
        ))
    path = tmp_path / "failing.jsonl"
    trajectory.save(str(path))
    return str(path), trajectory


def test_the_tool_is_a_dry_run_unless_live_is_true(tmp_path):
    path, trajectory = _write_trajectory(tmp_path)

    payload = record_replay_payload({"trajectory_path": path})

    assert payload["dry_run"] is True
    assert payload["trajectory_id"] == trajectory.id
    assert [action["status"] for action in payload["actions"]] == ["planned", "planned"]
    assert payload["summary"]["verified"] == 0
    assert payload["summary"]["all_effect_verified"] is None
    assert payload["ok"] is True
    assert payload["failed_steps"] == []
    # nothing was delivered, so no step may claim it was
    assert all(action["sent"] is False for action in payload["actions"])


def test_a_missing_path_is_an_error_not_an_empty_report(tmp_path):
    with pytest.raises(ValueError):
        record_replay_payload({})

    with pytest.raises(FileNotFoundError):
        record_replay_payload({"trajectory_path": str(tmp_path / "nope.jsonl")})


def test_live_replay_keeps_the_failed_steps_in_the_payload(tmp_path, monkeypatch):
    """A step that never reaches its expectation is failed, with its reason."""
    path, _ = _write_click_trajectory(tmp_path)

    def _fake_replayer(**kwargs):
        return Replayer(
            dry_run=kwargs["dry_run"], backend=_NoEffectBackend(),
            retries=1, timeout=0.01, interval=0.0,
        )

    monkeypatch.setattr("openeyes.mcp.replay_tool.Replayer", _fake_replayer)

    payload = record_replay_payload({"trajectory_path": path, "live": True})

    assert payload["dry_run"] is False
    assert payload["ok"] is False
    assert payload["summary"]["failed"] == 2
    assert [step["index"] for step in payload["failed_steps"]] == [0, 1]
    assert all(step["reason"] for step in payload["failed_steps"])
    # the full action list survives; the summary does not replace it
    assert [action["status"] for action in payload["actions"]] == ["failed", "failed"]
    assert json.loads(json.dumps(payload))["summary"]["failed"] == 2


def test_call_tool_exposes_record_replay(tmp_path):
    pytest.importorskip("mcp")
    import openeyes.mcp.server as mcp_server

    path, _ = _write_trajectory(tmp_path)

    result = asyncio.run(
        mcp_server.call_tool("record_replay", {"trajectory_path": path}),
    )
    payload = json.loads(result[0].text)

    assert payload["dry_run"] is True
    assert payload["ok"] is True
    assert len(payload["actions"]) == 2
    assert "failed_steps" in payload


def test_call_tool_reports_a_bad_path_in_the_error_envelope(tmp_path):
    pytest.importorskip("mcp")
    import openeyes.mcp.server as mcp_server

    result = asyncio.run(mcp_server.call_tool(
        "record_replay", {"trajectory_path": str(tmp_path / "nope.jsonl")},
    ))
    payload = json.loads(result[0].text)

    assert payload["tool"] == "record_replay"
    assert payload["error"]


class _NoEffectBackend:
    """A backend that records calls and never touches mouse or keyboard."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def click_selector(self, hwnd, selector, *, button="left", double=False):
        self.calls.append(("click_selector", hwnd, selector, button, double))

    def click_xy(self, x, y, *, button="left", double=False):
        self.calls.append(("click_xy", x, y, button, double))

    def probe(self, spec, *, hwnd=0):
        return {"found": False, "observed": "probe present=False"}
