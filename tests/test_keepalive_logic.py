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
