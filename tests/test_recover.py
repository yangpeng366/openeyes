"""Tests for session recovery.

Recovery is the part of a keepalive loop that runs when the thing it
watches is *gone*, so its contract is:

* a session that is still there is left alone - recovery never fires at
  a healthy target;
* a lost session is chased in the right order: the process first (a
  session cannot come back inside a program that is not running), then
  the session itself;
* the state has to **settle** - a session that is halfway up is not a
  session;
* the whole chase fits in one budget, and a budget that runs out is
  reported as a failure with the deadline named;
* a dry run never acts.
"""
from __future__ import annotations

from openeyes.core.recover import DEFAULT_DEADLINE, recover_session


def _no_sleep(_seconds: float) -> None:
    """Record the sleeps instead of performing them."""
    _no_sleep.calls.append(_seconds)


_no_sleep.calls = []


def _fresh_sleeps() -> None:
    _no_sleep.calls = []


_fresh_sleeps()


# ------------------------------------------------------------- the easy case
def test_a_session_that_is_up_is_left_alone():
    """Recovery is for a lost session; a healthy one is not touched."""
    acted = []

    report = recover_session(
        session_up=lambda: True,
        ensure_process=lambda: acted.append("process"),
        reconnect=lambda: acted.append("reconnect"),
        sleep=_no_sleep,
    )

    assert acted == []
    assert report.ok is True
    assert report.recovered is True
    assert report.attempts == 0
    assert report.stages == []


# -------------------------------------------------------------- the sequence
def test_the_process_is_started_before_the_session_is_reconnected():
    """A session cannot come back inside a program that is not running."""
    order = []
    session = {"up": False}

    def ensure_process():
        order.append("process")
        return {"ok": True}

    def reconnect():
        order.append("reconnect")
        session["up"] = True
        return {"ok": True}

    report = recover_session(
        session_up=lambda: session["up"],
        ensure_process=ensure_process,
        reconnect=reconnect,
        deadline=1.0,
        interval=0.01,
        sleep=_no_sleep,
    )

    assert order == ["process", "reconnect"]
    assert report.ok is True
    assert [s.name for s in report.stages] == ["detect", "process",
                                               "reconnect"]


def test_a_session_that_comes_back_on_its_own_is_not_reconnected():
    """Reconnecting on sight would fight the app."""
    reconnected = []
    session = {"up": False}

    def ensure_process():
        session["up"] = True       # the session returns with the process
        return {"ok": True}

    report = recover_session(
        session_up=lambda: session["up"],
        ensure_process=ensure_process,
        reconnect=lambda: reconnected.append(1),
        sleep=_no_sleep,
    )

    assert reconnected == []
    assert report.ok is True
    assert "reconnect" not in [s.name for s in report.stages]


def test_a_reconnect_that_brings_nothing_back_is_not_a_success():
    """Delivered is not effective - the rule the whole repo runs on."""
    report = recover_session(
        session_up=lambda: False,
        reconnect=lambda: {"ok": True},
        deadline=0.2,
        interval=0.05,
        sleep=_no_sleep,
    )

    assert report.ok is False
    assert report.recovered is False
    assert report.error is not None
    assert "0.2s" in report.error


# -------------------------------------------------------------- the budget
def test_the_recovery_fits_in_its_budget():
    """A budget that runs out is a failure, and it is reported."""
    report = recover_session(
        session_up=lambda: False,
        reconnect=lambda: {"ok": True},
        deadline=0.2,
        interval=0.05,
        sleep=_no_sleep,
    )

    assert report.ok is False
    assert report.elapsed <= 0.2 + 0.05
    assert report.deadline == 0.2


def test_the_default_budget_is_twenty_seconds():
    """The promise this direction is measured against: back within 20s."""
    assert DEFAULT_DEADLINE == 20.0


def test_the_report_carries_the_recovery_time():
    report = recover_session(
        session_up=lambda: True,
        sleep=_no_sleep,
    )
    assert report.recovery_seconds >= 0.0
    payload = report.to_dict()
    assert payload["recovery_seconds"] == round(report.elapsed, 3)
    assert payload["deadline"] == DEFAULT_DEADLINE


# --------------------------------------------------------------- stability
def test_a_session_that_never_settles_is_not_a_recovery():
    """A session halfway up is not a session."""
    session = {"up": False}

    def reconnect():
        session["up"] = True
        return {"ok": True}

    report = recover_session(
        session_up=lambda: session["up"],
        reconnect=reconnect,
        stable=lambda: False,          # it never holds still
        deadline=0.3,
        interval=0.05,
        sleep=_no_sleep,
    )

    assert report.recovered is True
    assert report.stable is False
    assert report.ok is False
    assert "stable" in (report.error or "")


def test_a_session_that_holds_still_is_a_recovery():
    session = {"up": False}
    seen = []

    def reconnect():
        session["up"] = True
        return {"ok": True}

    def stable():
        seen.append(1)
        return True

    report = recover_session(
        session_up=lambda: session["up"],
        reconnect=reconnect,
        stable=stable,
        stable_checks=2,
        deadline=1.0,
        interval=0.01,
        sleep=_no_sleep,
    )

    assert len(seen) >= 2          # held still twice in a row
    assert report.ok is True
    assert report.stable is True


# ----------------------------------------------------------------- dry run
def test_a_dry_run_never_acts():
    def boom():
        raise AssertionError("a dry run must not act")

    report = recover_session(
        session_up=lambda: False,
        ensure_process=boom,
        reconnect=boom,
        dry_run=True,
        sleep=_no_sleep,
    )

    assert report.dry_run is True
    assert report.ok is False
    assert report.recovered is False
    assert "dry-run" in report.detail


# --------------------------------------------------------------- robustness
def test_a_raising_probe_counts_as_not_up():
    """A transient UI error must not abort the recovery."""
    def broken():
        raise RuntimeError("the tree is not ready")

    report = recover_session(
        session_up=broken,
        reconnect=broken,
        deadline=0.1,
        interval=0.05,
        sleep=_no_sleep,
    )

    assert report.ok is False
    assert report.error is not None


def test_the_report_serialises_every_number():
    report = recover_session(
        session_up=lambda: False,
        reconnect=lambda: {"ok": True, "detail": "clicked"},
        deadline=0.1,
        interval=0.05,
        sleep=_no_sleep,
    )
    payload = report.to_dict()
    for key in ("ok", "recovered", "stable", "elapsed", "recovery_seconds",
                "deadline", "attempts", "dry_run", "error", "detail",
                "stages"):
        assert key in payload
    assert payload["stages"][0]["name"] == "detect"



# ------------------------------------------------------------ the budget
def test_a_stage_is_told_the_budget_that_is_left():
    """One budget is a promise the code keeps, not a comment.

    A stage that can take a while (launching an app) is handed the time
    that is left, so it can size its own timeout to it.
    """
    seen = {}
    session = {"up": False}

    def ensure_process(budget=0.0):
        seen["process_budget"] = budget
        return {"ok": True}

    def reconnect(budget=0.0):
        seen["reconnect_budget"] = budget
        session["up"] = True
        return {"ok": True}

    report = recover_session(session_up=lambda: session["up"],
                             ensure_process=ensure_process,
                             reconnect=reconnect,
                             deadline=10.0, interval=0.01,
                             sleep=_no_sleep)

    assert report.ok is True
    assert 0 < seen["process_budget"] <= 10.0
    assert 0 < seen["reconnect_budget"] <= seen["process_budget"]


def test_a_program_whose_session_never_returns_is_not_waited_for():
    """The wait for "it came back by itself" can be switched off.

    Any VPN's session does not return when the app is relaunched - it
    opens waiting for a click. Waiting for it would spend the budget on
    something that will not happen.
    """
    sleeps = []
    session = {"up": False}

    def ensure_process():
        return {"ok": True}

    def reconnect():
        session["up"] = True
        return {"ok": True}

    report = recover_session(session_up=lambda: session["up"],
                             ensure_process=ensure_process,
                             reconnect=reconnect,
                             settle_budget=0.0,
                             deadline=1.0, interval=0.5,
                             sleep=sleeps.append)

    assert report.ok is True
    assert sleeps == []       # it did not wait for a session that never came


def test_the_wait_for_a_self_returning_session_is_bounded():
    """The wait has a ceiling - it cannot eat the whole budget.

    The wait is a share of the deadline, so the rest of the budget is
    still there for the reconnect that actually brings the session back.
    """
    session = {"up": False}

    def ensure_process():
        return {"ok": True}

    report = recover_session(session_up=lambda: session["up"],
                             ensure_process=ensure_process,
                             deadline=0.2, interval=0.05,
                             sleep=_no_sleep)

    assert report.ok is False               # nothing came back
    assert report.elapsed < report.deadline  # the wait stopped short
    assert report.error is not None


def test_the_primitive_is_exported_from_the_package():
    """One core, many exits: the primitive is part of the public API."""
    from openeyes import recover_session as exported

    assert exported is recover_session