"""Tests for the attention budget.

The budget exists to protect the human, so the tests below pin the
refusals as hard as the grants: a budget that hands out one
interruption too many is worse than no budget at all, and a refusal
that is not counted is a refusal nobody ever learns from.
"""
from __future__ import annotations

from openeyes.agent.attention import AttentionBudget


# ------------------------------------------------------------------ the refusal
def test_no_budget_means_no_interruption():
    """Zero per minute means the agent may never ask to be looked at."""
    budget = AttentionBudget(0)
    assert budget.allows() is False
    assert budget.spend() is False
    assert budget.refused == 1


def test_a_negative_budget_is_read_as_zero():
    budget = AttentionBudget(-3)
    assert budget.per_minute == 0
    assert budget.allows() is False


def test_a_refused_interruption_is_counted():
    """The interruption the user did not have to suffer is still a fact."""
    budget = AttentionBudget(1)
    assert budget.spend() is True
    assert budget.spend() is False
    assert budget.refused == 1
    assert budget.used == 1
    assert budget.exhausted is True


# -------------------------------------------------------------------- the taking
def test_an_agent_that_needs_no_attention_spends_nothing():
    """Nothing to say to the human means no cursor, and no budget burnt."""
    budget = AttentionBudget(2)
    assert budget.take(needs_attention=False) is False
    assert budget.used == 0
    assert budget.refused == 0
    assert budget.allows() is True


def test_taking_attention_spends_the_budget():
    budget = AttentionBudget(2)
    assert budget.take(needs_attention=True) is True
    assert budget.used == 1
    assert budget.exhausted is False


def test_an_exhausted_budget_sends_the_agent_to_the_queue():
    """No allowance left means it waits - it does not interrupt."""
    budget = AttentionBudget(1)
    assert budget.take(needs_attention=True) is True
    assert budget.take(needs_attention=True) is False
    assert budget.used == 1
    assert budget.refused == 1


# ----------------------------------------------------------------- the sliding
def test_the_budget_window_slides():
    """An interruption an hour ago does not count against this minute."""
    clock = {"now": 1000.0}
    budget = AttentionBudget(1, clock=lambda: clock["now"], window_s=60.0)
    assert budget.take(needs_attention=True) is True
    clock["now"] += 61.0
    assert budget.used == 0
    assert budget.exhausted is False
    assert budget.take(needs_attention=True) is True
    assert budget.used == 1


# -------------------------------------------------------------------- the state
def test_the_budget_reports_its_own_state():
    budget = AttentionBudget(3)
    budget.take(needs_attention=True)
    state = budget.to_dict()
    assert state["per_minute"] == 3
    assert state["used"] == 1
    assert state["refused"] == 0
    assert state["exhausted"] is False


def test_a_storm_of_quiet_announcements_disturbs_nobody():
    """Nothing to say means nothing happened - and nothing is counted."""
    budget = AttentionBudget(1)
    for _ in range(50):
        assert budget.take(needs_attention=False) is False
    assert budget.used == 0
    assert budget.refused == 0
    assert budget.disturbance == 0


def test_the_refusal_count_is_the_disturbance_measure():
    """Only interruptions the user did not have to suffer are counted."""
    budget = AttentionBudget(2)
    assert budget.take(needs_attention=True) is True
    assert budget.take(needs_attention=True) is True
    for _ in range(4):
        assert budget.take(needs_attention=True) is False
    assert budget.refused == 4
    assert budget.used == 2
    assert budget.disturbance == 4


def test_a_budget_of_zero_protects_nothing_and_proves_nothing():
    """No budget was granted, so no refusal is evidence of disturbance."""
    budget = AttentionBudget(0)
    assert budget.take(needs_attention=True) is False
    assert budget.refused == 1
    assert budget.disturbance == 0
