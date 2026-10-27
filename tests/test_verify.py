"""Tests for the act-then-verify primitives."""
from __future__ import annotations

import time

from openeyes.core.verify import act_until, wait_until


def test_wait_until_returns_true_as_soon_as_condition_holds():
    state = {"ready": False}

    def condition():
        return state["ready"]

    state["ready"] = True
    assert wait_until(condition, timeout=0.5, interval=0.01) is True


def test_wait_until_times_out_when_condition_never_holds():
    started = time.monotonic()
    assert wait_until(lambda: False, timeout=0.2, interval=0.05) is False
    assert time.monotonic() - started >= 0.15


def test_wait_until_treats_a_raising_condition_as_not_yet():
    def boom():
        raise RuntimeError("UI not ready")

    started = time.monotonic()
    assert wait_until(boom, timeout=0.2, interval=0.05) is False
    assert time.monotonic() - started >= 0.15


def test_act_until_reports_success_on_the_first_attempt():
    calls = []
    report = act_until(lambda: calls.append("act") or "clicked",
                       lambda: True, timeout=0.2, interval=0.01)
    assert report["ok"] is True
    assert report["attempts"] == 1
    assert report["result"] == "clicked"
    assert calls == ["act"]


def test_act_until_retries_until_the_expectation_holds():
    """A delivered action is not an effective action — retry until it takes."""
    state = {"effective": False}
    calls = []

    def act():
        calls.append("act")
        if len(calls) >= 2:
            state["effective"] = True
        return "clicked"

    report = act_until(act, lambda: state["effective"],
                       retries=3, timeout=0.2, interval=0.01)
    assert report["ok"] is True
    assert report["attempts"] == 2
    assert len(calls) == 2


def test_act_until_fails_when_the_expectation_never_holds():
    calls = []
    report = act_until(lambda: calls.append("act"),
                       lambda: False, retries=2, timeout=0.1, interval=0.01)
    assert report["ok"] is False
    assert report["attempts"] == 2
    assert len(calls) == 2


def test_act_until_reports_an_action_error_without_raising():
    def act():
        raise RuntimeError("element vanished")

    report = act_until(act, lambda: False, retries=1, timeout=0.1, interval=0.01)
    assert report["ok"] is False
    assert "element vanished" in report["error"]


def test_act_until_calls_on_retry_between_attempts():
    seen = []
    report = act_until(lambda: None, lambda: False, retries=3,
                       timeout=0.1, interval=0.01,
                       on_retry=lambda attempt, result: seen.append(attempt))
    assert report["ok"] is False
    assert seen == [1, 2]


def test_act_until_lets_the_caller_vary_the_strategy_between_attempts():
    """The AnyVPN keepalive alternates Invoke and a pixel click."""
    methods = []
    state = {"done": False}

    def act():
        method = "invoke" if len(methods) % 2 == 0 else "pixel"
        methods.append(method)
        if method == "pixel":
            state["done"] = True
        return method

    report = act_until(act, lambda: state["done"], retries=2,
                       timeout=0.2, interval=0.01)
    assert report["ok"] is True
    assert methods == ["invoke", "pixel"]
