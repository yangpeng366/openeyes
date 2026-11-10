"""Tests for the Any VPN keepalive decision logic and the new selector fields."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from openeyes.core.schema import BBox, Center, Element
from openeyes.core.selector import find_elements

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "examples" / "anyvpn_keepalive.py"


def _load_keepalive():
    """Load examples/anyvpn_keepalive.py as a module (it is not a package)."""
    spec = importlib.util.spec_from_file_location("anyvpn_keepalive", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


pytestmark = pytest.mark.skipif(sys.platform != "win32",
                                reason="the keepalive example targets Windows")


@pytest.fixture(scope="module")
def kv():
    return _load_keepalive()


# ------------------------------------------------------------ countdown ---
def test_parse_countdown_reads_minutes_and_seconds(kv):
    assert kv.parse_countdown("0:18 left - click to reset") == 18
    assert kv.parse_countdown("1:05 left - click to reset") == 65
    assert kv.parse_countdown("0:00 left - click to reset") == 0


def test_parse_countdown_without_timer_is_none(kv):
    assert kv.parse_countdown("click to reset") is None
    assert kv.parse_countdown("") is None
    assert kv.parse_countdown(None) is None


def test_parse_countdown_ignores_case(kv):
    assert kv.parse_countdown("0:07 LEFT - click to reset") == 7


# ------------------------------------------------------- renewal window ---
def test_should_renew_only_inside_the_window(kv):
    assert kv.should_renew(9, 10) is True
    assert kv.should_renew(10, 10) is True
    assert kv.should_renew(11, 10) is False
    assert kv.should_renew(None, 10) is False


def test_seconds_until_due(kv):
    assert kv.seconds_until_due(19, 10) == 9.0
    assert kv.seconds_until_due(10, 10) == 0.0
    assert kv.seconds_until_due(3, 10) == 0.0
    assert kv.seconds_until_due(None, 10) == 0.0


# ------------------------------------------------------------- verdicts ---
def test_judge_renewal_hit_when_countdown_jumps_back_up(kv):
    # the countdown is a live label: only a jump proves the click landed
    assert kv.judge_renewal(9, 19) == "hit"
    assert kv.judge_renewal(0, 20) == "hit"


def test_judge_renewal_miss_when_countdown_keeps_falling(kv):
    assert kv.judge_renewal(9, 8) == "miss"
    assert kv.judge_renewal(9, 9) == "miss"


def test_judge_renewal_gone_when_button_vanishes(kv):
    assert kv.judge_renewal(9, None) == "gone"


def test_judge_renewal_unknown_without_a_countdown(kv):
    assert kv.judge_renewal(None, 19) == "unknown"


# ------------------------------------------------- library: automation id ---
def _mk(name: str, aid: str = "", ct: str = "Button") -> Element:
    return Element(
        backend="uia", control_type=ct, name=name,
        bbox=BBox(0, 0, 100, 30), center=Center(50, 15), automation_id=aid,
    )


def test_find_elements_matches_automation_id():
    elems = [
        _mk("0:18 left - click to reset", "KeepFreeAliveButton"),
        _mk("Secure my connection", "ConnectButton"),
    ]
    hit = find_elements(elems, automation_id="KeepFreeAliveButton")
    assert len(hit) == 1
    assert hit[0].automation_id == "KeepFreeAliveButton"


def test_find_elements_automation_id_is_exact():
    elems = [_mk("Reset", "ResetButton"), _mk("Reset now", "ResetButtonExtra")]
    assert [e.automation_id for e in find_elements(elems, automation_id="ResetButton")] == [
        "ResetButton"]


def test_find_elements_combines_automation_id_and_control_type():
    elems = [_mk("KeepFreeAliveButton", "KeepFreeAliveButton", ct="Text")]
    assert find_elements(elems, automation_id="KeepFreeAliveButton",
                         control_type="Button") == []


# --------------------------------------------------- the recovery chain ---
def test_the_recovery_budget_is_twenty_seconds(kv):
    """The acceptance number: a dropped session is back within 20s."""
    assert kv.RECOVERY_DEADLINE == 20.0
    assert kv.RECOVERY_SETTLE == 2.0


def test_the_health_verdict_is_a_pure_read(kv):
    """The verdict comes from the state alone - the probe cannot act.

    The old probe relaunched the app and clicked "Secure my connection"
    while it was reading, so a dry run really launched Any VPN.
    """
    down = kv.decide_health(kv.SessionState(app_up=False),
                             trigger=10, interval=20.0)
    assert down.ok is False
    assert down.due_in == 0.0

    session_down = kv.decide_health(
        kv.SessionState(app_up=True, hwnd=7, session_up=False),
        trigger=10, interval=20.0)
    assert session_down.ok is False

    healthy = kv.decide_health(
        kv.SessionState(app_up=True, hwnd=7, session_up=True, countdown=19),
        trigger=10, interval=20.0)
    assert healthy.ok is True
    assert healthy.due_in == 9.0

    due = kv.decide_health(
        kv.SessionState(app_up=True, hwnd=7, session_up=True, countdown=9),
        trigger=10, interval=20.0)
    assert due.ok is False
    assert due.due_in == 0.0


def test_the_recovery_runs_launch_then_reconnect(kv, monkeypatch):
    """A dropped session comes back in the order the chain names."""
    state = {"app": False, "up": False}
    order = []

    def fake_state():
        return kv.SessionState(app_up=state["app"],
                                hwnd=4242 if state["app"] else None,
                                session_up=state["up"],
                                countdown=15 if state["up"] else None,
                                detail="")

    def fake_relaunch(**_kwargs):
        order.append("launch")
        state["app"] = True
        return True

    def fake_connect(_hwnd, *, dry_run):
        order.append("reconnect")
        state["up"] = True
        return True, "clicked"

    monkeypatch.setattr(kv, "read_session_state", fake_state)
    monkeypatch.setattr(kv, "find_anyvpn_window", lambda: 4242)
    monkeypatch.setattr(kv, "relaunch_app", fake_relaunch)
    monkeypatch.setattr(kv, "connect", fake_connect)

    report = kv.recover_anyvpn(dry_run=False, deadline=5.0, interval=0.01,
                               settle=0.0)

    assert order == ["launch", "reconnect"]
    assert report.ok is True
    assert report.recovery_seconds <= 5.0


def test_a_recovery_that_brings_nothing_back_is_not_a_recovery(kv, monkeypatch):
    """Delivered is not effective - the rule the whole repo runs on."""
    monkeypatch.setattr(kv, "read_session_state",
                        lambda: kv.SessionState(app_up=False))
    monkeypatch.setattr(kv, "find_anyvpn_window", lambda: None)
    monkeypatch.setattr(kv, "relaunch_app", lambda **_kwargs: True)

    report = kv.recover_anyvpn(dry_run=False, deadline=0.2, interval=0.01,
                               settle=0.0)

    assert report.ok is False
    assert report.recovered is False
    assert report.error is not None


def test_a_dry_run_never_launches_the_app(kv, monkeypatch):
    """The red line: a dry run touches nothing.

    The health probe used to relaunch Any VPN, so a dry run really
    launched the app. The probe is a read now; the recovery is the
    action, and a dry run never calls the action.
    """
    acted = []

    def boom(*args, **kwargs):
        acted.append(args)
        raise AssertionError("a dry run must not act")

    monkeypatch.setattr(kv, "relaunch_app", boom)
    monkeypatch.setattr(kv, "connect", boom)
    monkeypatch.setattr(kv, "find_anyvpn_window", lambda: None)
    monkeypatch.setattr(kv, "read_session_state",
                        lambda: kv.SessionState(app_up=False))
    monkeypatch.setattr(sys, "argv", ["anyvpn_keepalive.py", "--bootstrap",
                                      "--max-iter", "1"])

    assert kv.main() == 0
    assert acted == []