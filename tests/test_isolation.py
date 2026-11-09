"""Tests for the isolation verdict.

The verdict decides one thing: may this work stay on the desktop the
human is using? The tests below pin the three triggers, the boundary
where a measurement counts as "too much", the precedence between the
levels, and - the part that matters most in front of a user - that a
verdict can be explained: every reason has to name the condition, the
threshold and the measurement.
"""
from __future__ import annotations

import pytest

from openeyes.agent.attention import AttentionBudget
from openeyes.agent.isolation import (
    ALL_LEVELS,
    LEVEL_NONE,
    LEVEL_SESSION,
    LEVEL_VM,
    IsolationConfig,
    IsolationVerdict,
    evaluate_isolation,
)
from openeyes.agent.scheduler import Scheduler


# ------------------------------------------------------------- nothing trips
def test_a_clean_desktop_keeps_the_agents_where_they_are():
    """No symptom, no escalation, and no reason to tell the user."""
    verdict = evaluate_isolation()
    assert verdict.escalate is False
    assert verdict.level == LEVEL_NONE
    assert verdict.reasons == []


def test_a_second_agent_that_is_merely_awake_is_not_parallelism():
    """Two agents on one desktop are only parallel when the work is."""
    verdict = evaluate_isolation(concurrent_writers=2)
    assert verdict.escalate is False
    assert verdict.level == LEVEL_NONE
    assert verdict.reasons == []


# -------------------------------------------------------------- the triggers
def test_real_parallelism_needs_a_vm():
    """Two writers at once cannot share one foreground window."""
    verdict = evaluate_isolation(needs_parallel=True, concurrent_writers=2)
    assert verdict.escalate is True
    assert verdict.level == LEVEL_VM
    assert len(verdict.reasons) == 1
    assert "真并行" in verdict.reasons[0]


def test_a_wide_semantic_miss_needs_a_session():
    """When the semantic path is gone, the agent needs its own foreground."""
    verdict = evaluate_isolation(semantic_miss_rate=0.8)
    assert verdict.escalate is True
    assert verdict.level == LEVEL_SESSION
    assert len(verdict.reasons) == 1
    assert "语义路径失效率" in verdict.reasons[0]


def test_a_repeatedly_exhausted_budget_needs_a_session():
    """A budget that keeps refusing means the work disturbs the human."""
    verdict = evaluate_isolation(budget_exhausted=5)
    assert verdict.escalate is True
    assert verdict.level == LEVEL_SESSION
    assert len(verdict.reasons) == 1
    assert "打扰预算耗尽次数" in verdict.reasons[0]


# --------------------------------------------------------------- the edges
def test_a_measurement_exactly_on_the_threshold_trips_it():
    """The threshold is inclusive: sitting on it is too much."""
    assert evaluate_isolation(semantic_miss_rate=0.5).level == LEVEL_SESSION
    assert evaluate_isolation(budget_exhausted=3).level == LEVEL_SESSION
    parallel = evaluate_isolation(needs_parallel=True, concurrent_writers=2)
    assert parallel.level == LEVEL_VM


def test_a_measurement_one_step_below_the_threshold_stays_put():
    """Almost too much is not too much."""
    assert evaluate_isolation(semantic_miss_rate=0.49).escalate is False
    assert evaluate_isolation(budget_exhausted=2).escalate is False
    almost = evaluate_isolation(needs_parallel=True, concurrent_writers=1)
    assert almost.escalate is False
    assert almost.level == LEVEL_NONE


def test_a_config_moves_the_thresholds():
    """The thresholds are configuration, not a constant in the code."""
    strict = IsolationConfig(semantic_miss_rate_threshold=0.1,
                             budget_exhausted_threshold=1)
    verdict = evaluate_isolation(semantic_miss_rate=0.2, budget_exhausted=1,
                                  config=strict)
    assert verdict.level == LEVEL_SESSION
    assert len(verdict.reasons) == 2


# ------------------------------------------------------------- the precedence
def test_a_vm_condition_outranks_a_session_condition():
    """When both fire, the further environment wins."""
    verdict = evaluate_isolation(needs_parallel=True, concurrent_writers=3,
                                  semantic_miss_rate=0.9, budget_exhausted=9)
    assert verdict.escalate is True
    assert verdict.level == LEVEL_VM
    assert len(verdict.reasons) == 3


def test_two_session_conditions_keep_the_session_level():
    """Neither of the two session reasons asks for a machine."""
    verdict = evaluate_isolation(semantic_miss_rate=0.9, budget_exhausted=9)
    assert verdict.level == LEVEL_SESSION
    assert len(verdict.reasons) == 2


# -------------------------------------------------------------- the reasons
def test_a_reason_names_the_condition_the_threshold_and_the_measurement():
    """A recommendation a user cannot understand is one they ignore."""
    verdict = evaluate_isolation(semantic_miss_rate=0.75, budget_exhausted=4)
    assert verdict.reasons
    for reason in verdict.reasons:
        assert "阈值" in reason
        assert "实测" in reason
    assert "0.50" in verdict.reasons[0]
    assert "0.75" in verdict.reasons[0]
    assert "3 次/分钟" in verdict.reasons[1]
    assert "4 次" in verdict.reasons[1]


def test_the_parallel_reason_carries_the_writer_count():
    """The number of writers is the evidence for the strongest level."""
    verdict = evaluate_isolation(needs_parallel=True, concurrent_writers=5)
    assert "concurrent_writers=5" in verdict.reasons[0]
    assert "concurrent_writers >= 2" in verdict.reasons[0]


# ------------------------------------------------------------ the data shape
def test_to_dict_is_plain_data():
    """The verdict has to survive JSON on its way to a host."""
    verdict = evaluate_isolation(semantic_miss_rate=0.9)
    data = verdict.to_dict()
    assert set(data) == {"escalate", "level", "reasons"}
    assert data["escalate"] is True
    assert data["level"] == LEVEL_SESSION
    assert data["reasons"] == verdict.reasons
    assert data["reasons"] is not verdict.reasons


def test_the_levels_are_the_documented_ones():
    """A caller switches on the level; the set must not drift."""
    assert ALL_LEVELS == (LEVEL_NONE, LEVEL_SESSION, LEVEL_VM)
    for level in ALL_LEVELS:
        assert IsolationVerdict(escalate=False, level=level).level == level


# ------------------------------------------------------------- the refusals
def test_a_measurement_outside_its_range_is_refused():
    """Nonsense in, nothing out: the caller gets told."""
    with pytest.raises(ValueError):
        evaluate_isolation(semantic_miss_rate=1.5)
    with pytest.raises(ValueError):
        evaluate_isolation(budget_exhausted=-1)
    with pytest.raises(ValueError):
        evaluate_isolation(concurrent_writers=-1)
    with pytest.raises(ValueError):
        IsolationConfig(semantic_miss_rate_threshold=0)
    with pytest.raises(ValueError):
        IsolationConfig(budget_exhausted_threshold=0)
    with pytest.raises(ValueError):
        IsolationConfig(parallel_min_agents=0)


# ------------------------------------------------------------ the way back down
def test_the_verdict_can_come_back_down():
    """Upgrading is a stop-loss, not a one-way door."""
    up = evaluate_isolation(needs_parallel=True, concurrent_writers=3,
                            semantic_miss_rate=0.9, budget_exhausted=9)
    assert up.escalate is True
    assert up.level == LEVEL_VM

    down = evaluate_isolation(needs_parallel=False, concurrent_writers=1,
                              semantic_miss_rate=0.0, budget_exhausted=0)
    assert down.escalate is False
    assert down.level == LEVEL_NONE
    assert down.reasons == []


def test_a_recovered_session_comes_back_to_the_shared_desktop():
    """The session level is a step, not a sentence."""
    up = evaluate_isolation(semantic_miss_rate=0.8)
    assert up.level == LEVEL_SESSION

    down = evaluate_isolation(semantic_miss_rate=0.1)
    assert down.escalate is False
    assert down.level == LEVEL_NONE


# ------------------------------------------------- what counts as disturbance
def test_only_real_refusals_count_as_disturbance():
    """The interruptions the user did not have to suffer."""
    budget = AttentionBudget(1)
    assert budget.take(needs_attention=True) is True
    for _ in range(3):
        assert budget.take(needs_attention=True) is False
    assert budget.disturbance == 3

    verdict = evaluate_isolation(budget_exhausted=budget.disturbance)
    assert verdict.escalate is True
    assert verdict.level == LEVEL_SESSION


def test_an_agent_with_nothing_to_say_disturbs_nobody():
    """A request that was never made is no evidence of anything."""
    budget = AttentionBudget(1)
    for _ in range(20):
        assert budget.take(needs_attention=False) is False
    assert budget.refused == 0
    assert budget.used == 0

    verdict = evaluate_isolation(budget_exhausted=budget.disturbance)
    assert verdict.escalate is False
    assert verdict.level == LEVEL_NONE


def test_a_budget_that_was_never_granted_is_not_exhausted():
    """Zero means the agent is misconfigured, not that the desk is small."""
    budget = AttentionBudget(0)
    for _ in range(5):
        assert budget.take(needs_attention=True) is False
    assert budget.refused == 5
    assert budget.disturbance == 0

    verdict = evaluate_isolation(budget_exhausted=budget.disturbance)
    assert verdict.escalate is False
    assert verdict.level == LEVEL_NONE


def test_a_scheduler_clash_is_not_a_disturbance():
    """Two agents fighting over a target is contention, not noise."""
    scheduler = Scheduler()
    scheduler.claim("worker-a", "hwnd:123", priority=1)
    for index in range(5):
        scheduler.claim(f"worker-{index}", "hwnd:123", priority=9 + index)
    assert len(scheduler.conflicts()) == 5

    verdict = evaluate_isolation(budget_exhausted=0)
    assert verdict.escalate is False
    assert verdict.level == LEVEL_NONE
