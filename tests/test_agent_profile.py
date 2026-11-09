"""Tests for the agent profile and the disturbance budget.

The profile says who an agent is and what it may take from the user; the
budget counts what it took. The defaults are the safe ones, and the
tests below pin them - a budget that silently allows what it should
refuse is worse than no budget at all.
"""
from __future__ import annotations

import pytest

from openeyes.agent.profile import (
    MODE_ACT,
    MODE_OBSERVE,
    MODE_SHADOW,
    AgentProfile,
    FocusBudget,
    color_for,
)


# ------------------------------------------------------------------ the profile
def test_the_default_profile_is_the_safe_one():
    profile = AgentProfile()
    assert profile.agent_id == "default"
    assert profile.mode == MODE_ACT
    assert profile.focus_budget_per_min == 0
    assert profile.allow_foreground is False
    assert profile.color


def test_the_colour_follows_the_identity():
    """The same identity always looks the same on screen."""
    assert color_for("worker-a") == color_for("worker-a")
    assert AgentProfile(agent_id="worker-a").color == color_for("worker-a")


def test_an_explicit_colour_is_kept():
    assert AgentProfile(color="#123456").color == "#123456"


def test_an_unknown_mode_is_refused():
    with pytest.raises(ValueError):
        AgentProfile(mode="yolo")


def test_a_negative_budget_is_refused():
    with pytest.raises(ValueError):
        AgentProfile(focus_budget_per_min=-1)


def test_only_an_acting_agent_may_act():
    assert AgentProfile(mode=MODE_ACT).may_act is True
    assert AgentProfile(mode=MODE_OBSERVE).may_act is False
    assert AgentProfile(mode=MODE_SHADOW).may_act is False


def test_a_profile_makes_its_own_budget():
    profile = AgentProfile(agent_id="w", focus_budget_per_min=5)
    budget = profile.new_budget()
    assert budget.per_minute == 5
    assert budget.allows() is True


# -------------------------------------------------------------------- the budget
def test_no_budget_means_no_steal():
    budget = FocusBudget(0)
    assert budget.allows() is False
    assert budget.spend() is False
    assert budget.refused == 1
    assert budget.exhausted is False


def test_a_budget_is_spent_and_then_refuses():
    budget = FocusBudget(2)
    assert budget.spend() is True
    assert budget.spend() is True
    assert budget.used == 2
    assert budget.spend() is False
    assert budget.exhausted is True
    assert budget.refused == 1


def test_the_budget_window_slides():
    """A steal an hour ago does not count against this minute."""
    clock = {"now": 1000.0}
    budget = FocusBudget(1, clock=lambda: clock["now"], window_s=60.0)
    assert budget.spend() is True
    clock["now"] += 61.0
    assert budget.used == 0
    assert budget.spend() is True


def test_the_budget_reports_its_own_state():
    budget = FocusBudget(1)
    budget.spend()
    state = budget.to_dict()
    assert state["per_minute"] == 1
    assert state["used"] == 1
    assert state["refused"] == 0
    # A budget of one, spent once, is used up.
    assert state["exhausted"] is True
