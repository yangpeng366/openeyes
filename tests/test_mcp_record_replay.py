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


def _write_blind_click_trajectory(tmp_path, *, steps: int = 2) -> tuple[str, Trajectory]:
    """Clicks that recorded no expectation: deliverable, never provable."""
    trajectory = Trajectory.new("mcp-live-replay-blind")
    for index in range(steps):
        trajectory.add(UIAEvent(
            ts=float(index + 1), action="click", element=dict(_ELEMENT),
            selector="automation_id=btn_submit", hwnd=4242,
        ))
    path = tmp_path / "blind.jsonl"
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


def test_every_report_names_delivery_separately_from_effect(tmp_path):
    """``delivered``/``refused_activation`` are contract, not decoration.

    A dry run attempts nothing: ``delivered`` stays ``None`` so a reader can
    tell "never arrived" from "never tried".
    """
    path, _ = _write_trajectory(tmp_path)

    payload = record_replay_payload({"trajectory_path": path})

    assert {"delivered", "not_delivered", "refused_activation"} <= set(
        payload["summary"])
    assert payload["summary"]["delivered"] is None
    assert payload["summary"]["not_delivered"] is None
    assert payload["summary"]["refused_activation"] == 0


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
    # no activation was requested, so the receipt is present but empty
    assert all(step["activation"] is None for step in payload["failed_steps"])
    # the full action list survives; the summary does not replace it
    assert [action["status"] for action in payload["actions"]] == ["failed", "failed"]
    assert json.loads(json.dumps(payload))["summary"]["failed"] == 2


# ---------------------------------------------------------------------------
# The ``activate`` contract
# ---------------------------------------------------------------------------

def _capture_replayer(monkeypatch, backend) -> dict:
    """Patch the payload builder's Replayer, keeping every kwarg it passes.

    The factory builds a real :class:`Replayer` around ``backend`` and records
    the arguments, so a test can assert what the tool asked for *and* what the
    replayer then did.
    """
    seen: dict = {}

    def _factory(**kwargs):
        seen.update(kwargs)
        return Replayer(
            dry_run=kwargs["dry_run"], backend=backend,
            retries=kwargs.get("retries", 2), timeout=kwargs.get("timeout", 2.0),
            interval=kwargs.get("interval", 0.0),
            activate=kwargs.get("activate", False),
        )

    monkeypatch.setattr("openeyes.mcp.replay_tool.Replayer", _factory)
    return seen


def _live_args(path: str, **extra) -> dict:
    args = {"trajectory_path": path, "live": True,
            "retries": 1, "timeout": 0.01, "interval": 0.0}
    args.update(extra)
    return args


def test_a_dry_run_never_activates_even_when_asked(tmp_path, monkeypatch):
    """``activate=true`` without ``live`` must not move a window."""
    path, _ = _write_click_trajectory(tmp_path)
    backend = _ActivatingBackend()
    seen = _capture_replayer(monkeypatch, backend)

    payload = record_replay_payload({"trajectory_path": path, "activate": True})

    assert seen["dry_run"] is True
    assert seen["activate"] is False
    assert backend.calls == []
    assert [a["status"] for a in payload["actions"]] == ["planned", "planned"]
    assert all(a["activation"] is None for a in payload["actions"])
    assert payload["failed_steps"] == []
    assert payload["ok"] is True


def test_live_activation_is_opt_in(tmp_path, monkeypatch):
    path, _ = _write_click_trajectory(tmp_path)
    backend = _ActivatingBackend(present=True)
    seen = _capture_replayer(monkeypatch, backend)

    record_replay_payload(_live_args(path))

    assert seen["activate"] is False
    assert [c[0] for c in backend.calls] == ["click_selector", "click_selector"]


def test_live_activate_activates_once_per_hwnd(tmp_path, monkeypatch):
    path, _ = _write_click_trajectory(tmp_path)
    backend = _ActivatingBackend(present=True)
    seen = _capture_replayer(monkeypatch, backend)

    payload = record_replay_payload(_live_args(path, activate=True))

    assert seen["activate"] is True
    assert [c[0] for c in backend.calls].count("activate") == 1
    assert backend.calls[0] == ("activate", 4242)
    assert all(a["activation"] == {"ok": True, "hwnd": 4242}
               for a in payload["actions"])
    assert payload["ok"] is True
    # both clicks were actually delivered, and neither was refused
    assert payload["summary"]["delivered"] == 2
    assert payload["summary"]["not_delivered"] == 0
    assert payload["summary"]["refused_activation"] == 0


def test_a_refused_activation_reaches_failed_steps_with_its_receipt(
        tmp_path, monkeypatch):
    """Activation failure is the reason nothing was sent - keep the receipt."""
    path, _ = _write_click_trajectory(tmp_path)
    backend = _ActivatingBackend(activate_ok=False)
    _capture_replayer(monkeypatch, backend)

    payload = record_replay_payload(_live_args(path, activate=True))

    assert payload["ok"] is False
    # the refusal is cached per hwnd, so both steps fail without delivery
    assert [s["index"] for s in payload["failed_steps"]] == [0, 1]
    for failed in payload["failed_steps"]:
        assert failed["activation"]["ok"] is False
        assert failed["activation"]["hwnd"] == 4242
        assert "not activated" in failed["reason"]
    assert [a["sent"] for a in payload["actions"]] == [False, False]
    assert [c[0] for c in backend.calls] == ["activate"]
    # the split must say "nothing arrived", not merely "the effect is unproven"
    summary = payload["summary"]
    assert summary["delivered"] == 0
    assert summary["not_delivered"] == 2
    assert summary["refused_activation"] == 2


# ---------------------------------------------------------------------------
# ``ok=True`` is not "the effect was proven"
# ---------------------------------------------------------------------------

def test_a_delivered_but_unprovable_step_is_ok_and_unverified(
        tmp_path, monkeypatch):
    """No recorded expectation: delivered, unjudged, still ``ok``.

    ``send`` sets ``sent``/``delivered``; nothing sets ``verified``. A reader
    who equates ``ok`` with "it worked" is wrong here, so the payload has to
    carry the counter-fact: ``all_effect_verified`` is ``False`` (a real live
    run, effect unproven) and never ``None`` (which means dry run). Pinning
    both halves stops a later "tidy-up" from making ``ok`` mean effect - and
    stops the delivered count from silently dropping to 0 for an unjudged
    step.
    """
    path, _ = _write_blind_click_trajectory(tmp_path)
    backend = _ActivatingBackend(present=True)
    _capture_replayer(monkeypatch, backend)

    payload = record_replay_payload(_live_args(path, activate=True))
    summary = payload["summary"]

    assert payload["dry_run"] is False
    assert (summary["verified"], summary["unverified"], summary["failed"]) == (
        0, 2, 0)
    assert summary["all_effect_verified"] is False
    assert payload["ok"] is True
    assert payload["failed_steps"] == []
    assert [a["status"] for a in payload["actions"]] == [
        "unverified", "unverified"]
    assert all(a["sent"] is True for a in payload["actions"])
    # delivered, and nothing was refused activation
    assert summary["delivered"] == 2
    assert summary["not_delivered"] == 0
    assert summary["refused_activation"] == 0
    # both actions left the process, so the split covers the total
    assert summary["delivered"] + summary["not_delivered"] == summary["total"]
    # and each step says why it could not be judged
    assert all(a["expectation"] is None for a in payload["actions"])
    assert all(a["expectation_source"] == "no expectation recorded"
               for a in payload["actions"])
    assert all("no expectation recorded" in a["reason"]
               for a in payload["actions"])


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
    # the delivery split survives the MCP envelope, and a dry run claims none
    assert payload["summary"]["delivered"] is None
    assert payload["summary"]["not_delivered"] is None
    assert payload["summary"]["refused_activation"] == 0


def test_call_tool_reports_the_delivery_split_for_a_refused_activation(
        tmp_path, monkeypatch):
    """Over the real MCP envelope, a refusal reads as "not delivered".

    ``delivered + not_delivered`` must cover every action, and
    ``refused_activation`` must name the subset that never left the process -
    a caller who only looks at ``failed`` cannot tell those apart otherwise.
    """
    pytest.importorskip("mcp")
    import openeyes.mcp.server as mcp_server

    path, _ = _write_click_trajectory(tmp_path)
    _capture_replayer(monkeypatch, _ActivatingBackend(activate_ok=False))

    result = asyncio.run(mcp_server.call_tool(
        "record_replay", _live_args(path, activate=True),
    ))
    payload = json.loads(result[0].text)

    assert payload["ok"] is False
    summary = payload["summary"]
    assert summary["delivered"] == 0
    assert summary["not_delivered"] == 2
    assert summary["refused_activation"] == 2
    assert summary["delivered"] + summary["not_delivered"] == summary["total"]
    assert summary["refused_activation"] <= summary["not_delivered"]
    assert [s["activation"]["ok"] for s in payload["failed_steps"]] == [
        False, False]


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


class _ActivatingBackend:
    """Acts and activates; records the call order, never touches the desktop.

    The activation is cached per hwnd by the replayer, so a second step on the
    same window must not produce a second ``activate`` call.
    """

    def __init__(self, *, activate_ok: bool = True, present: bool = False):
        self.activate_ok = activate_ok
        self.present = present
        self.calls: list[tuple] = []

    def activate(self, hwnd):
        self.calls.append(("activate", hwnd))
        return {"ok": self.activate_ok, "hwnd": hwnd}

    def click_selector(self, hwnd, selector, *, button="left", double=False):
        self.calls.append(("click_selector", hwnd, selector))
        self.present = True

    def click_xy(self, x, y, *, button="left", double=False):
        self.calls.append(("click_xy", x, y))
        self.present = True

    def send_hotkey(self, combo):
        self.calls.append(("send_hotkey", combo))

    def type_text(self, text):
        self.calls.append(("type_text", text))

    def probe(self, spec, *, hwnd=0):
        return {"found": self.present, "observed": f"present={self.present}"}
