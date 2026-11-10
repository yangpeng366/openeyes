"""Tests for the two-agent demo, with no desktop in the way.

Plan (``docs/TEST-PLAN.md`` §1):

| Field | Value |
| --- | --- |
| **Behaviour** | The demo shows two cursors, one per agent, in two colours; every drawn frame lands in the ledger under the agent that drew it; and the run fails when the user's foreground window changed. |
| **Layer** | `unit` - the window is a stand-in and the foreground is a probe. The live desktop is proved by running the script itself (see its docstring), not by these tests. |
| **How it is proved** | the positions the stand-in was asked to draw; the per-agent counts read back out of the ledger; the exit code of the verdict; the ledger export, recounted by the report. |
| **Risk** | a demo that moves the user's focus, or a ledger that cannot tell the agents apart, makes the whole mouse cluster unshowable - and a demo that *claims* two cursors while one is refused makes the report lie. |

The attribution rule matters here too: the demo's own hand never touches
the pointer, so ``physical_input`` must stay false on every entry. The
tests pin that rather than trusting the prose.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from openeyes.agent.overlay import OverlayState
from openeyes.agent.profile import AgentProfile, color_for

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "examples" / "mouse-cluster-demo.py"
REPORT = REPO / "examples" / "interference-report.py"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def demo_module():
    return _load(SCRIPT, "mouse_cluster_demo")


@pytest.fixture(scope="module")
def report_module():
    return _load(REPORT, "interference_report")


class _FakeOverlay:
    """A cursor that records where it was asked to draw, and nothing else.

    It has the same surface as the real overlay - identity, colour, state,
    ``show`` / ``hide`` / ``close`` - so the demo cannot tell the
    difference, and the test can read what the demo did without a screen.
    """

    def __init__(self, agent_id: str = "default", label: str = "",
                 color: str = "", size: int = 0) -> None:
        profile = AgentProfile(agent_id=agent_id, label=label, color=color)
        self.agent_id = profile.agent_id
        self.label = profile.label
        self.color = profile.color
        self.state = OverlayState(agent_id=profile.agent_id,
                                 label=profile.label, color=profile.color)
        self.shows: list[tuple[int, int]] = []
        self.calls: list[str] = []

    def show(self, x: int, y: int) -> bool:
        self.shows.append((int(x), int(y)))
        self.calls.append("show")
        self.state.x = int(x)
        self.state.y = int(y)
        self.state.visible = True
        return True

    def hide(self) -> None:
        self.calls.append("hide")
        self.state.visible = False

    def close(self) -> None:
        self.calls.append("close")
        self.state.visible = False


def _foreground_readings(*values: int | None):
    """A probe that hands out the given readings, then repeats the last."""
    remaining = list(values)

    def read() -> int | None:
        if len(remaining) > 1:
            return remaining.pop(0)
        return remaining[0]

    return read


def _demo(demo_module, *, seconds: float = 0.5, fps: float = 10.0,
          attention: int = 2, foreground=(7, 7)):
    """A demo wired to stand-in cursors and a scripted foreground."""
    overlays: list[_FakeOverlay] = []

    def factory(**kwargs):
        overlay = _FakeOverlay(**kwargs)
        overlays.append(overlay)
        return overlay

    demo = demo_module.MouseClusterDemo(
        overlay_factory=factory,
        attention_per_min=attention,
        fps=fps,
        bounds=demo_module.Rect(0, 0, 800, 600),
        foreground=_foreground_readings(*foreground),
        pointer=lambda: (10, 20),
        sleep=lambda _seconds: None,
    )
    result = demo.run(seconds)
    demo.close()
    return demo, overlays, result


# ------------------------------------------------------------------ two agents
def test_the_two_agents_look_different(demo_module):
    """Two identities, two colours, two paths - or there is nothing to see."""
    _demo_obj, overlays, _result = _demo(demo_module)
    assert len(overlays) == 2
    assert overlays[0].color != overlays[1].color
    assert overlays[0].label != overlays[1].label
    assert (overlays[0].agent_id, overlays[1].agent_id) == ("worker-a", "worker-b")
    # The colours come from the identity, not from a list of favourites.
    assert overlays[0].color == color_for("worker-a")
    assert overlays[1].color == color_for("worker-b")


def test_each_agent_draws_inside_its_own_lane(demo_module):
    """Two cursors on one screen must not sit on top of each other."""
    demo, overlays, _result = _demo(demo_module)
    lanes = [agent.area for agent in demo.agents]
    assert lanes[0].left + lanes[0].width <= lanes[1].left
    for overlay, lane in zip(overlays, lanes, strict=True):
        assert overlay.shows, "every shown agent must have drawn something"
        for x, y in overlay.shows:
            assert lane.left <= x <= lane.left + lane.width
            assert lane.top <= y <= lane.top + lane.height


def test_the_two_paths_are_not_the_same_path(demo_module):
    """If both cursors followed one path there would be one cursor."""
    a = demo_module.figure_eight(0.25, 1.0, demo_module.Rect(0, 0, 400, 400))
    b = demo_module.sweep(0.25, 1.0, demo_module.Rect(0, 0, 400, 400))
    assert a != b
    again = demo_module.figure_eight(0.25, 1.0, demo_module.Rect(0, 0, 400, 400))
    assert a == again, "the path must be a function of time, not of chance"


# --------------------------------------------------------------- the ledger
def test_the_ledger_tells_the_agents_apart(demo_module):
    demo, _overlays, result = _demo(demo_module, seconds=1.0, fps=10.0)
    entries = demo.ledger.entries()
    per_agent = {agent.agent_id: sum(1 for e in entries
                                     if e.agent_id == agent.agent_id)
                 for agent in result.agents}
    assert set(per_agent) == {"worker-a", "worker-b"}
    assert all(count > 0 for count in per_agent.values())
    assert sum(per_agent.values()) == len(entries) == result.entries
    assert {agent.agent_id: agent.entries for agent in result.agents} == per_agent


def test_every_entry_names_its_agent_and_its_cost(demo_module):
    demo, _overlays, _result = _demo(demo_module, seconds=0.5, fps=10.0)
    entries = demo.ledger.entries()
    assert entries, "a run must leave a trace"
    for entry in entries:
        assert entry.agent_id in ("worker-a", "worker-b")
        # The demo draws pictures: it never delivers input and never takes
        # the focus. That is the whole claim, and it is per entry.
        assert entry.physical_input is False
        assert entry.focus_stolen is False
        assert entry.cursor_moved is True


def test_the_pointer_is_read_but_never_driven(demo_module):
    demo, overlays, result = _demo(demo_module)
    assert result.pointer_before == result.pointer_after == (10, 20)
    for overlay in overlays:
        assert set(overlay.calls) <= {"show", "hide", "close"}


# ------------------------------------------------------------- the verdict
def test_the_focus_verdict_names_what_changed(demo_module):
    ok, reason = demo_module.focus_verdict(7, 7)
    assert ok is True and "unchanged" in reason
    ok, reason = demo_module.focus_verdict(7, 8)
    assert ok is False
    assert "7" in reason and "8" in reason
    # An unreadable probe is not evidence of theft.
    ok, reason = demo_module.focus_verdict(None, 8)
    assert ok is True and "unavailable" in reason


def test_a_clean_run_exits_zero(demo_module):
    _demo_obj, overlays, result = _demo(demo_module, seconds=0.5, fps=5.0)
    assert result.focus_ok is True
    assert result.cursors_ok is True
    assert all(overlay.shows for overlay in overlays)
    assert result.exit_code() == 0


def test_a_changed_foreground_fails_the_run(demo_module):
    """The user kept the focus, or the demo is not shippable."""
    _demo_obj, _overlays, result = _demo(demo_module, foreground=(11, 12))
    assert result.focus_ok is False
    assert result.exit_code() == 3


# ------------------------------------------------------------- the budget
def test_a_budget_refusal_draws_nothing(demo_module):
    """One interruption a minute, two agents: the second waits, and says so."""
    demo, overlays, result = _demo(demo_module, attention=1)
    assert result.agents[0].shown is True
    assert overlays[0].shows, "the first agent drew its cursor"
    assert result.agents[1].shown is False
    assert result.agents[1].refused is True
    assert overlays[1].shows == [], "a refused agent must draw nothing"
    assert result.exit_code() == 4

    refused = [e for e in demo.ledger.entries() if not e.ok]
    assert len(refused) == result.refused_entries == 1
    entry = refused[0]
    assert entry.agent_id == "worker-b"
    assert entry.detail == "BUDGET_EXCEEDED"
    assert entry.delivery == "none"
    assert entry.verified is False
    assert entry.physical_input is False


def test_the_budget_does_not_refuse_an_agent_with_budget_left(demo_module):
    demo, _overlays, result = _demo(demo_module, attention=2)
    assert result.refused_entries == 0
    assert all(agent.shown for agent in result.agents)


# ------------------------------------------------------------- the stand-in
def test_the_dry_overlay_creates_no_window(demo_module):
    """``--dry-run`` is only credible if the stand-in is what runs."""
    overlay = demo_module.DryOverlay(agent_id="worker-a", label="A")
    assert overlay.supported is True
    assert overlay.show(5, 6) is True
    assert overlay.shows == [(5, 6)]
    assert overlay.state.visible is True
    overlay.hide()
    assert overlay.state.visible is False
    assert getattr(overlay, "_hwnd", None) is None


def test_the_dry_run_walks_the_ledger_and_the_verdict(demo_module, tmp_path, capsys):
    """The CI form: no window, but the accounting and the verdict are real."""
    out = tmp_path / "ledger.jsonl"
    code = demo_module.main(["--dry-run", "--seconds", "0.2", "--fps", "5",
                             "--out", str(out)])
    printed = capsys.readouterr().out
    assert code == 0
    assert "dry-run" in printed
    assert "worker-a" in printed and "worker-b" in printed
    assert out.is_file()
    records = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert any(record.get("kind") == "summary" for record in records)


# ------------------------------------------------------- the closed loop
def test_the_export_and_the_report_agree(demo_module, report_module, tmp_path):
    """Step 6's claim: the four numbers are the entries, not a guess."""
    demo, _overlays, result = _demo(demo_module, seconds=1.0, fps=10.0)
    export = demo_module.write_export(tmp_path / "ledger.jsonl", demo.ledger,
                                      demo.refused_entries)
    report = report_module.summarise(report_module.load_records(export),
                                     path=str(export))
    assert report.ok is True
    assert report.totals.entries == result.entries
    assert report.totals.physical_input == 0
    assert report.totals.focus_stolen == 0
    assert report.totals.refused == result.refused_entries
    assert report.totals.verified == result.verified
    assert report.totals.entries == (result.agents[0].entries
                                     + result.agents[1].entries)


def test_a_tampered_claim_is_caught(demo_module, report_module, tmp_path):
    """A summary that cannot be wrong is a summary nobody checked."""
    demo, _overlays, _result = _demo(demo_module)
    export = demo_module.write_export(tmp_path / "ledger.jsonl", demo.ledger,
                                      demo.refused_entries)
    lines = export.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        record = json.loads(line)
        if record.get("kind") == "summary":
            record["verified"] = record["verified"] - 1
            lines[index] = json.dumps(record)
    export.write_text("\n".join(lines) + "\n", encoding="utf-8")

    report = report_module.summarise(report_module.load_records(export),
                                     path=str(export))
    assert report.diffs, "the report must notice the claim moved"
    assert report.diffs[0]["field"] == "verified"
    assert report_module.main(["--ledger", str(export)]) == 1
