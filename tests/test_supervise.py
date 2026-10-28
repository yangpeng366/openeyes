"""Tests for the supervision loop.

Keepalive, login and scheduled work are the same loop, so the loop's
contract is what has to hold:

* a healthy target is left alone;
* an unhealthy target is acted on, and the round counts as a success only
  when the expected state was **observed**;
* the next round is scheduled from the state, not from a fixed timer - a
  fixed timer against a deadline clicks exactly at the expiry;
* disturbance is attributed to the automation, not to the desktop.
"""
from __future__ import annotations

import pytest

from openeyes.core.supervise import Health, supervise


def _no_sleep(_seconds: float) -> None:
    """Record the sleeps instead of performing them."""
    _no_sleep.calls.append(_seconds)
    return None


_no_sleep.calls = []


@pytest.fixture(autouse=True)
def _reset_calls():
    _no_sleep.calls = []
    yield
    _no_sleep.calls = []


# ------------------------------------------------------------- healthy path
def test_a_healthy_target_is_left_alone():
    acted = []
    report = supervise(
        health=lambda: Health(ok=True, due_in=5.0, detail="fine"),
        act=lambda: acted.append(1),
        expect=lambda: False,
        max_rounds=3,
        sleep=_no_sleep,
    )
    assert acted == []
    assert report.healthy == 3
    assert report.actions == 0


def test_the_next_round_is_scheduled_from_the_state():
    """A state-driven cadence, not a fixed interval."""
    supervise(
        health=lambda: Health(ok=True, due_in=7.5),
        act=lambda: None,
        expect=lambda: False,
        max_rounds=3,
        sleep=_no_sleep,
    )
    assert _no_sleep.calls == [7.5, 7.5]


def test_a_missing_due_falls_back_to_the_interval():
    supervise(
        health=lambda: Health(ok=True, due_in=None),
        act=lambda: None,
        expect=lambda: False,
        max_rounds=2,
        interval=20.0,
        sleep=_no_sleep,
    )
    assert _no_sleep.calls == [20.0]


# ------------------------------------------------------------- acting path
def test_an_unhealthy_target_is_acted_on():
    acted = []
    report = supervise(
        health=lambda: Health(ok=False, due_in=1.0),
        act=lambda: acted.append(1) or {"ok": True, "method": "invoke"},
        expect=lambda: True,
        max_rounds=2,
        sleep=_no_sleep,
    )
    assert len(acted) == 2
    assert report.actions == 2
    assert report.effective == 2


def test_a_delivered_action_that_changes_nothing_is_a_miss():
    """The whole point: delivered is not effective."""
    report = supervise(
        health=lambda: Health(ok=False, due_in=1.0),
        act=lambda: {"ok": True, "method": "invoke"},
        expect=lambda: False,          # the state never moves
        max_rounds=1,
        retries=1,
        sleep=_no_sleep,
    )
    assert report.effective == 0
    assert report.misses == 1
    assert report.false_positive_rate == 1.0


def test_a_failure_to_deliver_is_not_counted_as_a_miss():
    report = supervise(
        health=lambda: Health(ok=False, due_in=1.0),
        act=lambda: None,              # nothing was delivered
        expect=lambda: False,
        max_rounds=1,
        retries=1,
        sleep=_no_sleep,
    )
    assert report.misses == 0
    assert report.failures == 1


def test_a_false_positive_rate_is_zero_when_every_success_was_real():
    report = supervise(
        health=lambda: Health(ok=False, due_in=1.0),
        act=lambda: {"ok": True},
        expect=lambda: True,
        max_rounds=4,
        sleep=_no_sleep,
    )
    assert report.effective == 4
    assert report.false_positive_rate == 0.0


def test_a_dry_run_never_acts():
    acted = []
    report = supervise(
        health=lambda: Health(ok=False, due_in=1.0),
        act=lambda: acted.append(1),
        expect=lambda: True,
        max_rounds=2,
        dry_run=True,
        sleep=_no_sleep,
    )
    assert acted == []
    assert report.actions == 2
    assert report.effective == 0


def test_a_round_is_reported_as_it_happens():
    seen = []
    supervise(
        health=lambda: Health(ok=False, due_in=0.0),
        act=lambda: {"ok": True},
        expect=lambda: True,
        max_rounds=2,
        on_round=seen.append,
        sleep=_no_sleep,
    )
    assert len(seen) == 2
    assert seen[0].index == 0
    assert seen[0].acted is True
    assert seen[0].effective is True


# ------------------------------------------------- outcome classification
def test_an_action_that_reports_failure_is_a_failure_not_a_miss():
    """The target was gone: the action never landed. Not a missed renewal."""
    report = supervise(
        health=lambda: Health(ok=False, due_in=1.0),
        act=lambda: {"ok": False, "method": "none", "verdict": "failed"},
        expect=lambda: False,
        max_rounds=1,
        retries=1,
        sleep=_no_sleep,
    )
    assert report.misses == 0
    assert report.failures == 1
    assert report.false_positive_rate == 0.0


def test_the_caller_verdict_travels_with_the_round():
    report = supervise(
        health=lambda: Health(ok=False, due_in=1.0),
        act=lambda: {"ok": True, "method": "invoke", "verdict": "hit"},
        expect=lambda: True,
        max_rounds=1,
        sleep=_no_sleep,
    )
    assert report.timeline[0].verdict == "hit"


def test_a_dry_run_still_reports_the_verdict():
    """A dry run says what it *would* do, so the operator sees the truth.

    The action is never called - a read-only ``preview`` probe supplies
    the verdict instead.
    """
    def boom():
        raise AssertionError("a dry run must not act")

    report = supervise(
        health=lambda: Health(ok=False, due_in=1.0),
        act=boom,
        preview=lambda: {"ok": False, "method": "none", "verdict": "failed"},
        expect=lambda: False,
        max_rounds=1,
        dry_run=True,
        sleep=_no_sleep,
    )
    assert report.timeline[0].verdict == "failed"
    assert report.failures == 1


def test_a_dry_run_does_not_count_a_working_action_as_a_failure():
    def boom():
        raise AssertionError("a dry run must not act")

    report = supervise(
        health=lambda: Health(ok=False, due_in=1.0),
        act=boom,
        preview=lambda: {"ok": True, "method": "invoke", "verdict": "hit"},
        expect=lambda: True,
        max_rounds=1,
        dry_run=True,
        sleep=_no_sleep,
    )
    assert report.failures == 0
    assert report.effective == 0


# -------------------------------------------------------------- interference
def test_a_user_moving_the_mouse_is_not_charged_to_the_automation():
    """The desktop's own mouse movement is not the loop's doing."""
    snapshots = [
        {"foreground": 111, "cursor": (1, 1), "target": 222},
        {"foreground": 111, "cursor": (9, 9), "target": 222},
    ]

    def snapshot():
        return snapshots.pop(0) if snapshots else snapshots_default()

    def snapshots_default():
        return {"foreground": 111, "cursor": (9, 9), "target": 222}

    report = supervise(
        health=lambda: Health(ok=False, due_in=0.0),
        act=lambda: {"ok": True, "method": "invoke"},
        expect=lambda: True,
        max_rounds=1,
        snapshot=snapshot,
        sleep=_no_sleep,
    )
    assert report.timeline[0].interference["cursor_moved"] is False
    assert report.interference_rate == 0.0


def test_a_pixel_click_that_landed_is_charged_to_the_automation():
    def snapshot():
        return {"foreground": 111, "cursor": (1, 1), "target": 222}

    report = supervise(
        health=lambda: Health(ok=False, due_in=0.0),
        act=lambda: {"ok": True, "method": "pixel", "landed": True},
        expect=lambda: True,
        max_rounds=1,
        snapshot=snapshot,
        sleep=_no_sleep,
    )
    assert report.timeline[0].interference["cursor_moved"] is True
    assert report.interference_rate == 1.0


def test_a_focus_already_on_the_target_is_not_a_theft():
    def snapshot():
        return {"foreground": 222, "cursor": (1, 1), "target": 222}

    report = supervise(
        health=lambda: Health(ok=False, due_in=0.0),
        act=lambda: {"ok": True, "method": "invoke"},
        expect=lambda: True,
        max_rounds=1,
        snapshot=snapshot,
        sleep=_no_sleep,
    )
    assert report.timeline[0].interference["focus_stolen"] is False


def test_a_focus_pulled_onto_the_target_is_a_theft():
    states = [{"foreground": 111, "cursor": (1, 1), "target": 222},
              {"foreground": 222, "cursor": (1, 1), "target": 222}]

    def snapshot():
        return states.pop(0) if states else states[0]

    report = supervise(
        health=lambda: Health(ok=False, due_in=0.0),
        act=lambda: {"ok": True, "method": "invoke"},
        expect=lambda: True,
        max_rounds=1,
        snapshot=snapshot,
        sleep=_no_sleep,
    )
    assert report.timeline[0].interference["focus_stolen"] is True
    assert report.interference_rate == 1.0


# ------------------------------------------------------------------ report
def test_the_report_serialises_every_number():
    report = supervise(
        health=lambda: Health(ok=True, due_in=1.0),
        act=lambda: None,
        expect=lambda: True,
        max_rounds=1,
        sleep=_no_sleep,
    )
    payload = report.to_dict()
    for key in ("rounds", "healthy", "actions", "effective", "misses",
                "failures", "false_positive_rate", "interference_rate",
                "timeline"):
        assert key in payload
