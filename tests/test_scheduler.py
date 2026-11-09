"""Tests for the conflict scheduler.

Several agents, one target. The tests below pin the two halves of the
rule: the holder keeps what it holds unless somebody outranks it, and
every clash is written down - a preemption included, because somebody
was pushed aside.
"""
from __future__ import annotations

from openeyes.agent.scheduler import Scheduler


# ------------------------------------------------------------------- the claim
def test_a_free_target_is_granted():
    scheduler = Scheduler()
    assert scheduler.claim("worker-a", "hwnd:123") is True
    assert scheduler.holder("hwnd:123") == "worker-a"


def test_a_second_agent_on_the_same_target_is_refused():
    scheduler = Scheduler()
    assert scheduler.claim("worker-a", "hwnd:123") is True
    assert scheduler.claim("worker-b", "hwnd:123") is False
    assert scheduler.holder("hwnd:123") == "worker-a"
    assert scheduler.conflicts() == []


def test_different_targets_do_not_collide():
    scheduler = Scheduler()
    assert scheduler.claim("worker-a", "hwnd:123") is True
    assert scheduler.claim("worker-b", "hwnd:456") is True
    assert scheduler.conflicts() == []


def test_equal_priority_does_not_preempt():
    """Two agents of the same rank must not take turns stealing."""
    scheduler = Scheduler()
    assert scheduler.claim("worker-a", "hwnd:123", priority=2) is True
    assert scheduler.claim("worker-b", "hwnd:123", priority=2) is False
    assert scheduler.holder("hwnd:123") == "worker-a"


# ---------------------------------------------------------------- the preemption
def test_a_higher_priority_takes_the_target_over():
    scheduler = Scheduler()
    assert scheduler.claim("worker-a", "hwnd:123", priority=1) is True
    assert scheduler.claim("worker-b", "hwnd:123", priority=5) is True
    assert scheduler.holder("hwnd:123") == "worker-b"

    clashes = scheduler.conflicts()
    assert len(clashes) == 1
    assert clashes[0].agent_a == "worker-a"
    assert clashes[0].agent_b == "worker-b"
    assert clashes[0].target == "hwnd:123"


def test_a_preempted_agent_no_longer_holds_the_target():
    scheduler = Scheduler()
    scheduler.claim("worker-a", "hwnd:123", priority=1)
    scheduler.claim("worker-b", "hwnd:123", priority=5)
    scheduler.release("worker-a")
    assert scheduler.holder("hwnd:123") == "worker-b"


# ------------------------------------------------------------------- the release
def test_a_released_target_can_be_claimed_again():
    scheduler = Scheduler()
    assert scheduler.claim("worker-a", "hwnd:123") is True
    scheduler.release("worker-a")
    assert scheduler.holder("hwnd:123") is None
    assert scheduler.claim("worker-b", "hwnd:123") is True
    assert scheduler.holder("hwnd:123") == "worker-b"


def test_releasing_an_unknown_agent_is_harmless():
    scheduler = Scheduler()
    scheduler.claim("worker-a", "hwnd:123")
    scheduler.release("worker-z")
    assert scheduler.holder("hwnd:123") == "worker-a"


# ------------------------------------------------------------------ the prediction
def test_would_conflict_predicts_without_touching_anything():
    """The prediction reads the state; it must not change it."""
    scheduler = Scheduler()
    scheduler.claim("worker-a", "hwnd:123")
    before = scheduler.to_dict()

    assert scheduler.would_conflict("worker-b", "hwnd:123") is True
    assert scheduler.would_conflict("worker-a", "hwnd:123") is False
    assert scheduler.would_conflict("worker-b", "hwnd:999") is False

    assert scheduler.to_dict() == before


# ---------------------------------------------------------------------- the state
def test_the_scheduler_reports_its_own_state():
    scheduler = Scheduler()
    scheduler.claim("worker-a", "hwnd:123")
    state = scheduler.to_dict()
    assert state["claims"] == {"hwnd:123": "worker-a"}
    assert state["conflicts"] == []
