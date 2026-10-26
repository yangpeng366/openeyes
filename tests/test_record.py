"""Tests for openeyes.record submodule.

Covers:
- Trajectory JSONl round-trip
- Recorder lifecycle + declarative APIs
- Replayer dry-run
- _build_selector priority
- merge_trajectories DAG
- to_natural_language rendering
- Export functions (codex_skill, action_receipt, opencua)

No real UI needed; uses fake element dicts throughout.
"""
from __future__ import annotations

import json

import pytest

from openeyes.decision import JevScore, JevScorer, ThresholdConfig, TypesafeJevScorer
from openeyes.record.abstract import (
    graph_to_natural_language,
    merge_trajectories,
    to_natural_language,
)
from openeyes.record.event import InputEvent, Trajectory, UIAEvent
from openeyes.record.export.action_receipt import to_action_receipt
from openeyes.record.export.codex_skill import to_codex_skill
from openeyes.record.export.opencua import to_opencua_trajectory
from openeyes.record.generator import generate_trajectory_from_prompt
from openeyes.record.recorder import Recorder, _build_selector
from openeyes.record.replayer import Replayer

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _fake_element(**overrides):
    """Build a minimal OpenEyes Element.to_dict() for testing."""
    base = {
        "backend": "uia",
        "control_type": "Button",
        "name": "Submit",
        "bbox": {"x": 100, "y": 200, "w": 80, "h": 30},
        "center": {"x": 140, "y": 215},
        "automation_id": "btn_submit",
        "class_name": "Button",
        "score": 1.0,
        "hint": "a",
        "interactive": True,
        "state": {"enabled": True, "visible": True},
        "parent_chain": [],
    }
    base.update(overrides)
    return base


class _FakeElement:
    """Stand-in for openeyes.core.schema.Element with .to_dict()."""
    def __init__(self, **overrides):
        self._d = _fake_element(**overrides)

    def to_dict(self):
        return dict(self._d)


# ---------------------------------------------------------------------------
# Trajectory
# ---------------------------------------------------------------------------

class TestTrajectory:
    def test_new_creates_uuid(self):
        t = Trajectory.new("test-traj", app_name="Edge")
        assert t.id
        assert t.name == "test-traj"
        assert t.app_name == "Edge"
        assert t.steps == []

    def test_add_and_close(self):
        t = Trajectory.new("t")
        t.add(InputEvent(ts=1.0, action="hotkey", combo="ctrl+a"))
        t.close()
        assert len(t.steps) == 1
        assert t.ended_at is not None

    def test_jsonl_roundtrip(self):
        t = Trajectory.new("rt", app_name="Notepad", description="round-trip test")
        t.add(UIAEvent(
            ts=1.0, action="click",
            element=_fake_element(),
            selector="automation_id=btn_submit",
            intent="click submit",
        ))
        t.add(InputEvent(ts=2.0, action="hotkey", combo="ctrl+s", intent="save"))
        t.add(InputEvent(ts=3.0, action="wait", duration_ms=200))
        t.close()

        text = t.to_jsonl()
        t2 = Trajectory.from_jsonl(text)
        assert t2.id == t.id
        assert t2.name == t.name
        assert len(t2.steps) == 3
        assert isinstance(t2.steps[0], UIAEvent)
        assert isinstance(t2.steps[1], InputEvent)
        assert t2.steps[1].combo == "ctrl+s"
        assert isinstance(t2.steps[2], InputEvent)
        assert t2.steps[2].duration_ms == 200

    def test_save_and_load(self, tmp_path):
        t = Trajectory.new("io-test")
        t.add(InputEvent(ts=1.0, action="wait", duration_ms=100))
        t.close()
        path = str(tmp_path / "traj.jsonl")
        t.save(path)
        t2 = Trajectory.load(path)
        assert t2.id == t.id
        assert len(t2.steps) == 1

    def test_chrome_bridge_jsonl_roundtrip(self):
        header = json.dumps({
            "_kind": "header", "id": "bridge-id", "name": "edge-five-step",
            "started_at": 1.0, "ended_at": 2.0, "app_name": "Edge",
            "description": "Real Edge capture", "step_count": 2,
            "source": "chrome-recorder-bridge", "url": "about:blank",
        })
        click = json.dumps({
            "ts": 1.1, "action": "click", "element": {"source": "cdp-browser"},
            "selector": "pointer=(41,18)", "app_name": "Edge",
            "window_title": "", "hwnd": 0, "pid": 0, "intent": "",
            "input_text": None,
            "meta": {"source": "cdp-browser", "x": 41, "y": 18},
        })
        keypress = json.dumps({
            "ts": 1.2, "action": "type", "element": {"source": "cdp-browser"},
            "selector": "combo=unknown", "app_name": "Edge",
            "window_title": "", "hwnd": 0, "pid": 0, "intent": "",
            "input_text": "a", "meta": {"source": "cdp-browser"},
        })
        trajectory = Trajectory.from_jsonl("\n".join([header, click, keypress]))
        assert [step.action for step in trajectory.steps] == ["click", "type"]
        assert trajectory.steps[0].element["source"] == "cdp-browser"
        assert trajectory.steps[1].input_text == "a"
        assert trajectory.id == "bridge-id"
        reparsed = Trajectory.from_jsonl(trajectory.to_jsonl())
        assert reparsed.steps == trajectory.steps


# ---------------------------------------------------------------------------
# _build_selector
# ---------------------------------------------------------------------------

class TestBuildSelector:
    def test_automation_id_first(self):
        assert _build_selector(_fake_element(automation_id="btn1")) == "automation_id=btn1"

    def test_name_fallback(self):
        ed = _fake_element(automation_id="")
        sel = _build_selector(ed)
        assert sel.startswith("name=Submit")

    def test_hint_fallback(self):
        ed = _fake_element(automation_id="", name="")
        sel = _build_selector(ed)
        assert sel.startswith("hint=")

    def test_center_fallback(self):
        ed = _fake_element(automation_id="", name="", hint="")
        sel = _build_selector(ed)
        assert sel.startswith("center=")


# ---------------------------------------------------------------------------
# Recorder
# ---------------------------------------------------------------------------

class TestRecorder:
    def test_lifecycle(self):
        r = Recorder()
        t = r.start(name="test", app_name="Edge")
        assert r.trajectory is t
        r.record_click(12345, _FakeElement(), intent="open menu")
        r.record_type(12345, _FakeElement(control_type="Edit", name="URL"), "https://example.com", intent="navigate")
        r.record_hotkey("ctrl+l", intent="focus address bar")
        r.record_wait(300, intent="wait for page")
        traj = r.stop()
        assert len(traj.steps) == 4
        assert traj.ended_at is not None
        assert r.trajectory is None

    def test_double_start_raises(self):
        r = Recorder()
        r.start()
        with pytest.raises(RuntimeError):
            r.start()

    def test_stop_without_start_raises(self):
        r = Recorder()
        with pytest.raises(RuntimeError):
            r.stop()

    def test_record_click_element_dict(self):
        """Recorder accepts raw dict (not just Element objects)."""
        r = Recorder()
        r.start()
        ev = r.record_click(99, _fake_element(name="OK"))
        assert ev.action == "click"
        assert ev.selector == "automation_id=btn_submit"
        r.stop()


# ---------------------------------------------------------------------------
# Replayer
# ---------------------------------------------------------------------------

class TestReplayer:
    def test_dry_run_builds_plan(self):
        r = Recorder()
        r.start(name="dry-run-test")
        r.record_click(100, _FakeElement(), intent="click submit")
        r.record_hotkey("ctrl+s")
        traj = r.stop()

        rp = Replayer(dry_run=True)
        report = rp.replay(traj)
        assert report.dry_run is True
        assert len(report.actions) == 2
        assert report.actions[0]["plan"]["kind"] == "click"
        assert report.actions[1]["plan"]["kind"] == "hotkey"
        assert not report.errors


# ---------------------------------------------------------------------------
# Abstract (merge + natural language)
# ---------------------------------------------------------------------------

class TestAbstract:
    def _make_traj(self, name, steps_data):
        t = Trajectory.new(name, app_name="TestApp")
        for sd in steps_data:
            if "element" in sd:
                t.add(UIAEvent(**sd))
            else:
                t.add(InputEvent(**sd))
        t.close()
        return t

    def test_merge_single_trajectory(self):
        t = self._make_traj("flow1", [
            {"ts": 1.0, "action": "click", "element": _fake_element(name="Open"),
             "selector": "automation_id=btn_open"},
            {"ts": 2.0, "action": "click", "element": _fake_element(name="Save"),
             "selector": "automation_id=btn_save"},
        ])
        graph = merge_trajectories([t], workflow_name="test-flow")
        assert len(graph.nodes) == 2
        assert len(graph.edges) == 1
        assert graph.workflow_name == "test-flow"

    def test_merge_deduplicates(self):
        t1 = self._make_traj("f1", [
            {"ts": 1.0, "action": "click", "element": _fake_element(name="A"),
             "selector": "automation_id=a"},
            {"ts": 2.0, "action": "click", "element": _fake_element(name="B"),
             "selector": "automation_id=b"},
        ])
        t2 = self._make_traj("f2", [
            {"ts": 1.0, "action": "click", "element": _fake_element(name="A"),
             "selector": "automation_id=a"},
            {"ts": 2.0, "action": "click", "element": _fake_element(name="B"),
             "selector": "automation_id=b"},
        ])
        graph = merge_trajectories([t1, t2])
        assert len(graph.nodes) == 2
        assert graph.nodes[0].count == 2

    def test_to_natural_language(self):
        t = Trajectory.new("nl-test", app_name="Notepad", description="test NL output")
        t.add(UIAEvent(ts=1.0, action="click", element=_fake_element(name="File"),
                        selector="name=File|type=MenuItem", intent="open file menu"))
        t.add(InputEvent(ts=2.0, action="hotkey", combo="ctrl+s", intent="save"))
        t.add(InputEvent(ts=3.0, action="wait", duration_ms=500))
        t.close()
        text = to_natural_language(t)
        assert "# nl-test" in text
        assert "ctrl+s" in text
        assert "500 ms" in text

    def test_graph_to_natural_language(self):
        t = self._make_traj("g", [
            {"ts": 1.0, "action": "click", "element": _fake_element(),
             "selector": "automation_id=btn_submit", "intent": "submit form"},
        ])
        graph = merge_trajectories([t])
        text = graph_to_natural_language(graph)
        assert "Nodes:" in text

    def test_graph_to_dot(self):
        t = self._make_traj("dot", [
            {"ts": 1.0, "action": "click", "element": _fake_element(name="A"),
             "selector": "automation_id=a"},
            {"ts": 2.0, "action": "click", "element": _fake_element(name="B"),
             "selector": "automation_id=b"},
        ])
        graph = merge_trajectories([t])
        dot = graph.to_dot()
        assert "digraph" in dot
        assert "->" in dot


# ---------------------------------------------------------------------------
# Export functions
# ---------------------------------------------------------------------------

class TestExports:
    def _sample_traj(self):
        t = Trajectory.new("export-test", app_name="Edge", description="sample for exports")
        t.add(UIAEvent(ts=1.0, action="click",
                        element=_fake_element(name="Search", control_type="Edit"),
                        selector="automation_id=search_box", intent="focus search"))
        t.add(UIAEvent(ts=2.0, action="type",
                        element=_fake_element(name="Search", control_type="Edit"),
                        selector="automation_id=search_box",
                        input_text="openeyes github", intent="type query"))
        t.add(InputEvent(ts=3.0, action="hotkey", combo="enter", intent="submit search"))
        t.close()
        return t

    def test_codex_skill_output(self):
        traj = self._sample_traj()
        md = to_codex_skill(traj, description="Search GitHub for OpenEyes")
        assert "openeyes" in md.lower() or "search" in md.lower()
        assert "---" in md  # YAML front-matter

    def test_action_receipt_output(self):
        traj = self._sample_traj()
        yaml_text = to_action_receipt(traj)
        assert "action_trace_receipt" in yaml_text
        assert "ui_action_event" in yaml_text
        assert traj.name in yaml_text  # workflow name appears as target

    def test_opencua_output(self):
        traj = self._sample_traj()
        jsonl = to_opencua_trajectory(traj)
        lines = [ln for ln in jsonl.splitlines() if ln.strip()]
        assert len(lines) >= 2  # header + steps
        header = json.loads(lines[0])
        assert header["trajectory_id"] == traj.id
        assert header["format"].startswith("opencua")


class TestJevGenerator:
    def _inventory(self):
        return [
            _fake_element(name="Search box", control_type="Edit",
                          automation_id="search_box"),
            _fake_element(name="Cancel", control_type="Button",
                          automation_id="cancel_button"),
        ]

    def _static_scorer(self, confidence, probability):
        class _Static(JevScorer):
            def ask(self, goal, candidates, *, app_name=""):
                return JevScore(target_index=0, confidence=confidence,
                                prob=probability, latency_ms=0)
        return _Static()

    def test_fake_jev_is_default_without_typesafe_key(self, monkeypatch):
        monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
        monkeypatch.delenv("TYPESAFE_BASE_URL", raising=False)
        trajectory = generate_trajectory_from_prompt(
            "Search box",
            app_name="Edge",
            element_inventory=self._inventory(),
            backend="jev",
        )
        assert len(trajectory.steps) == 1
        step = trajectory.steps[0]
        assert step.element["automation_id"] == "search_box"
        assert step.meta["decision_backend"] == "FakeJevScorer"
        assert step.meta["decision_band"] == "act"
        assert step.meta["thresholds"]["low_stakes"] == 0.60
        assert step.meta["thresholds"]["high_stakes"] == 0.85

    def test_real_jev_is_selected_for_a_local_node_without_key(self, monkeypatch):
        monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
        monkeypatch.setenv("TYPESAFE_BASE_URL", "http://127.0.0.1:9201")
        calls = []

        def _ask(self, goal, candidates, *, app_name=""):
            calls.append((goal, candidates, app_name))
            return JevScore(target_index=1, confidence=0.95,
                            prob=0.92, latency_ms=7)

        monkeypatch.setattr(TypesafeJevScorer, "ask", _ask)
        trajectory = generate_trajectory_from_prompt(
            "Cancel the dialog",
            app_name="Edge",
            element_inventory=self._inventory(),
            backend="jev",
        )
        assert calls
        assert trajectory.steps[0].meta["decision_backend"] == "TypesafeJevScorer"

    def test_real_jev_is_selected_only_with_typesafe_key(self, monkeypatch):
        monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
        calls = []

        def _ask(self, goal, candidates, *, app_name=""):
            calls.append((goal, candidates, app_name))
            return JevScore(target_index=1, confidence=0.95,
                            prob=0.92, latency_ms=7)

        monkeypatch.setattr(TypesafeJevScorer, "ask", _ask)
        trajectory = generate_trajectory_from_prompt(
            "Cancel the dialog",
            app_name="Edge",
            element_inventory=self._inventory(),
            backend="jev",
        )
        assert calls
        assert trajectory.steps[0].element["automation_id"] == "cancel_button"
        assert trajectory.steps[0].meta["decision_backend"] == "TypesafeJevScorer"

    def test_low_confidence_falls_back_to_empty_safe_trajectory(self):
        trajectory = generate_trajectory_from_prompt(
            "Unknown goal",
            app_name="Edge",
            element_inventory=self._inventory(),
            backend="jev",
            jev_scorer=self._static_scorer(0.55, 0.10),
        )
        assert trajectory.steps == []

    def test_uncertain_probability_requires_review_or_is_suppressed(self):
        trajectory = generate_trajectory_from_prompt(
            "Ambiguous goal",
            app_name="Edge",
            element_inventory=self._inventory(),
            backend="jev",
            jev_scorer=self._static_scorer(0.75, 0.50),
        )
        assert trajectory.steps == []

    def test_reviewable_confidence_is_marked_but_still_emitted(self):
        trajectory = generate_trajectory_from_prompt(
            "Search box",
            app_name="Edge",
            element_inventory=self._inventory(),
            backend="jev",
            jev_scorer=self._static_scorer(0.75, 0.20),
        )
        assert len(trajectory.steps) == 1
        assert trajectory.steps[0].meta["decision_band"] == "flag_for_review"
        assert trajectory.steps[0].meta["requires_review"] is True

    def test_jev_trajectory_matches_record_jsonl_schema(self):
        trajectory = generate_trajectory_from_prompt(
            "Search box",
            app_name="Edge",
            element_inventory=self._inventory(),
            backend="jev",
        )
        lines = trajectory.to_jsonl().splitlines()
        header = json.loads(lines[0])
        step = json.loads(lines[1])
        assert header["step_count"] == 1
        assert step["action"] == "click"
        assert step["selector"] == "automation_id=search_box"
        loaded = Trajectory.from_jsonl(trajectory.to_jsonl())
        assert loaded.steps[0].to_dict() == trajectory.steps[0].to_dict()

    def test_threshold_config_is_injected_for_contract_checks(self):
        thresholds = ThresholdConfig()
        trajectory = generate_trajectory_from_prompt(
            "Search box",
            app_name="Edge",
            element_inventory=self._inventory(),
            backend="jev",
            jev_scorer=self._static_scorer(0.86, 0.85),
            jev_thresholds=thresholds,
        )
        meta = trajectory.steps[0].meta
        assert meta["thresholds"]["high_stakes"] == thresholds.high_stakes_threshold
        assert meta["decision_band"] == "act"


# ---- hybrid_generate (path C) ----

class TestHybridGenerate:
    def _make_recorded(self):
        traj = Trajectory.new(name="test-hybrid", app_name="TestApp",
                              description="original description")
        traj.add(UIAEvent(
            ts=1000.0, action="click",
            element={"name": "OK", "control_type": "Button",
                     "automation_id": "btnOk", "interactive": True},
            selector="automation_id=btnOk", app_name="TestApp",
            intent="Click OK",
        ))
        traj.add(UIAEvent(
            ts=1001.0, action="type",
            element={"name": "Search", "control_type": "Edit",
                     "automation_id": "txtSearch", "interactive": True},
            selector="automation_id=txtSearch", app_name="TestApp",
            input_text="hello", intent="Type search term",
        ))
        traj.close()
        return traj

    def test_mock_passes_through(self):
        from openeyes.record.hybrid import hybrid_generate
        recorded = self._make_recorded()
        result = hybrid_generate(recorded, "enhanced description", backend="mock")
        assert result.name == recorded.name
        assert result.description == "enhanced description"
        assert len(result.steps) == 2
        assert result.steps[0].action == "click"
        assert result.steps[1].action == "type"

    def test_auto_without_llm_falls_back(self):
        from openeyes.record.hybrid import hybrid_generate
        recorded = self._make_recorded()
        result = hybrid_generate(recorded, "auto description", backend="auto")
        assert result.description == "auto description"
        assert len(result.steps) == 2

    def test_unknown_backend_raises(self):
        from openeyes.record.hybrid import hybrid_generate
        recorded = self._make_recorded()
        try:
            hybrid_generate(recorded, "x", backend="invalid")
            raise AssertionError("should raise")
        except ValueError:
            pass

    def test_few_shot_accepted(self):
        from openeyes.record.hybrid import hybrid_generate
        recorded = self._make_recorded()
        other = self._make_recorded()
        result = hybrid_generate(recorded, "with few-shot", backend="mock",
                                 few_shot=[other])
        assert len(result.steps) == 2

# ---- hybrid_generate transport contract (mock-transport integration) ----

class TestHybridGenerateTransport:
    """Verify the real-backend LLM path of hybrid_generate via a monkeypatched
    transport. These tests cover the contract that was implicit in round 597:
      - backend='real' returns LLM-refined steps when transport responds
      - backend='real' with no key raises LLMUnavailable (no silent fallback)
      - backend='real' falls back to mock when transport returns empty / malformed
      - backend='auto' with no key falls back to mock (already covered above)
    The point is to keep the contract tested without making a real network call.
    """

    def _make_recorded(self):
        traj = Trajectory.new(name="hl-test", app_name="TestApp",
                              description="orig")
        traj.add(UIAEvent(
            ts=1000.0, action="click",
            element={"name": "OK", "control_type": "Button",
                     "automation_id": "btnOk", "interactive": True},
            selector="automation_id=btnOk", app_name="TestApp",
            intent="Click OK",
        ))
        traj.close()
        return traj

    def test_real_with_canned_jsonl_returns_refined(self, monkeypatch):
        from openeyes.record import hybrid as hybrid_mod
        from openeyes.record.generator import LLMUnavailable

        canned = json.dumps({
            "action": "click",
            "element": {
                "name": "OK", "control_type": "Button",
                "automation_id": "btnOk", "interactive": True,
            },
            "selector": "automation_id=btnOk",
            "app_name": "TestApp",
            "intent": "Refined intent: confirm the dialog",
        })
        monkeypatch.setattr(hybrid_mod, "_call_llm", lambda *a, **kw: canned)

        recorded = self._make_recorded()
        result = hybrid_mod.hybrid_generate(
            recorded, "refined description", backend="real",
            api_key="dummy-key-not-used",
        )
        assert len(result.steps) == 1
        assert result.steps[0].action == "click"
        assert result.steps[0].meta.get("source") == "hybrid_llm"
        assert "Refined" in (result.steps[0].intent or "")
        # suppress unused import lint
        del LLMUnavailable

    def test_real_with_empty_response_falls_back_to_mock(self, monkeypatch):
        from openeyes.record import hybrid as hybrid_mod
        from openeyes.record.generator import LLMUnavailable

        monkeypatch.setattr(hybrid_mod, "_call_llm", lambda *a, **kw: "")
        recorded = self._make_recorded()
        result = hybrid_mod.hybrid_generate(
            recorded, "fallback description", backend="real",
            api_key="dummy-key-not-used",
        )
        # Empty transport → parser falls back to mock (recorded steps preserved).
        assert len(result.steps) == 1
        assert result.steps[0].meta.get("source") != "hybrid_llm"
        del LLMUnavailable

    def test_real_with_garbage_response_falls_back_to_mock(self, monkeypatch):
        from openeyes.record import hybrid as hybrid_mod
        from openeyes.record.generator import LLMUnavailable

        monkeypatch.setattr(
            hybrid_mod, "_call_llm", lambda *a, **kw: "not-jsonl\n{garbage",
        )
        recorded = self._make_recorded()
        result = hybrid_mod.hybrid_generate(
            recorded, "garbage description", backend="real",
            api_key="dummy-key-not-used",
        )
        assert len(result.steps) == 1
        assert result.steps[0].meta.get("source") != "hybrid_llm"
        del LLMUnavailable

    def test_real_without_key_raises_no_silent_fallback(self, monkeypatch):
        from openeyes.record import hybrid as hybrid_mod
        from openeyes.record.generator import LLMUnavailable

        # Ensure no key leaks from the environment for this assertion.
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        recorded = self._make_recorded()
        with pytest.raises(LLMUnavailable):
            hybrid_mod.hybrid_generate(
                recorded, "no key", backend="real",
            )