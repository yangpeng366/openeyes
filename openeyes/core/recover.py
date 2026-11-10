"""Bring a lost session back, and prove it came back.

A session can vanish in two ways, and both need the same answer: *is it
back yet?* An app closes its window when a session times out, a VPN drops
the tunnel, a browser loses the tab. The old keepalive loop could only
renew a session that was still there; once it was gone it had nothing to
say but "the control is missing" and left the user offline.

Recovery is a sequence with a budget, not a single action:

1. **detect** - the session is gone (poll for it: a session often comes
   back by itself, and a loop that reconnects on sight would fight the
   app);
2. **process** - the program that owns the session may be gone too, so
   start it (this is the "offline poll": no network, no session, only
   the local process);
3. **reconnect** - re-establish the session itself;
4. **stable** - wait until the state *holds still*. A session that is
   halfway up is not a session: the countdown has to tick, the window
   has to stay readable.

Every step is bounded by one deadline, because the promise this module
makes is a number: **a lost session is back within 20 seconds**
(:data:`DEFAULT_DEADLINE`). The report carries that number, so a caller
can assert on it instead of hoping.

.. code-block:: python

    from openeyes.core.recover import recover_session

    report = recover_session(
        session_up=lambda: read_keepalive(hwnd) is not None,
        ensure_process=lambda: relaunch_app(),
        reconnect=lambda: connect(hwnd),
        stable=lambda: countdown_is_ticking(),
        deadline=60.0,
    )
    report.ok             # the session is back AND stable
    report.recovery_seconds

Two rules keep the number honest:

* **The budget is one budget.** The stages share it; a stage that
  overruns does not push the total past the deadline.
* **Dry runs never act.** ``dry_run=True`` reports the plan and touches
  nothing - the same promise the CLI and the supervisor make.
"""
from __future__ import annotations

import inspect
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

#: The recovery budget: a lost session must be back within 20 seconds.
#: This is the number the keepalive is measured against, so it is the
#: default rather than a suggestion: a caller that needs more says so.
DEFAULT_DEADLINE = 20.0

#: How long to wait between polls while a stage is in progress.
DEFAULT_INTERVAL = 2.0


@dataclass
class RecoveryStage:
    """One step of the recovery, with what it actually did."""

    name: str
    ok: bool
    elapsed: float
    detail: str = ""

    def to_dict(self) -> dict:
        return {"name": self.name, "ok": self.ok,
                "elapsed": round(self.elapsed, 3), "detail": self.detail}


@dataclass
class RecoveryReport:
    """What a recovery attempt cost, and whether the session came back.

    Attributes:
        ok: the session is back **and** stable - the whole promise.
        recovered: the session came back.
        stable: the state held still afterwards.
        elapsed: seconds the whole attempt took.
        deadline: the budget it was held to.
        attempts: how many times a stage was tried.
        stages: what each step did, in order.
        dry_run: nothing was acted on.
        error: why it failed, when it failed.
    """

    ok: bool = False
    recovered: bool = False
    stable: bool = False
    elapsed: float = 0.0
    deadline: float = DEFAULT_DEADLINE
    attempts: int = 0
    stages: list[RecoveryStage] = field(default_factory=list)
    dry_run: bool = False
    error: str | None = None
    detail: str = ""

    @property
    def recovery_seconds(self) -> float:
        """Seconds until the session was back (the headline number)."""
        return self.elapsed

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "recovered": self.recovered,
            "stable": self.stable,
            "elapsed": round(self.elapsed, 3),
            "recovery_seconds": round(self.recovery_seconds, 3),
            "deadline": self.deadline,
            "attempts": self.attempts,
            "dry_run": self.dry_run,
            "error": self.error,
            "detail": self.detail,
            "stages": [s.to_dict() for s in self.stages],
        }


def _truthy(result: Any) -> bool:
    """Read a caller's report: a dict says so with ``ok``, else it is truthy."""
    if isinstance(result, dict):
        if "ok" in result:
            return bool(result["ok"])
        return True
    return bool(result)


def _detail_of(result: Any, fallback: str = "") -> str:
    if isinstance(result, dict):
        for key in ("detail", "error", "verdict"):
            value = result.get(key)
            if value:
                return str(value)
    if isinstance(result, str):
        return result
    return fallback


def _call(action: Callable[..., Any], budget: float) -> Any:
    """Call a stage action, telling it the budget when it wants to know.

    "One budget" is a promise the code can keep, not a comment: a stage
    that can take a while (launching an app) is told how much time is
    left, so it can size its own timeout to what is left.
    """
    try:
        params = inspect.signature(action).parameters
    except (TypeError, ValueError):
        params = {}
    if "budget" in params:
        return action(budget=budget)
    return action()


def _finish(stages: list[RecoveryStage], started: float, deadline: float,
            attempts: int, *, recovered: bool, stable: bool = False,
            detail: str, error: str | None = None) -> RecoveryReport:
    elapsed = time.monotonic() - started
    return RecoveryReport(ok=bool(recovered and stable and error is None),
                           recovered=bool(recovered),
                           stable=bool(stable),
                           elapsed=elapsed,
                           deadline=deadline,
                           attempts=attempts,
                           stages=stages,
                           detail=detail,
                           error=error)


def recover_session(*, session_up: Callable[[], bool],
                    ensure_process: Callable[[], Any] | None = None,
                    reconnect: Callable[[], Any] | None = None,
                    stable: Callable[[], bool] | None = None,
                    deadline: float = DEFAULT_DEADLINE,
                    interval: float = DEFAULT_INTERVAL,
                    stable_checks: int = 2,
                    settle_budget: float | None = None,
                    dry_run: bool = False,
                    sleep: Callable[[float], None] = time.sleep) -> RecoveryReport:
    """Recover a lost session inside a deadline, and report what happened.

    Args:
        session_up: probe for "the session is there". A raising probe
            counts as "not up" - a transient UI error must not abort the
            recovery.
        ensure_process: bring the owning program back (launch it, or
            restore its window). Optional: a session can die inside a
            program that is still running.
        reconnect: re-establish the session itself.
        stable: probe for "the state holds still". Optional; when it is
            absent, a recovered session counts as stable.
        deadline: the total budget in seconds. Every stage shares it.
        interval: seconds between polls.
        stable_checks: how many consecutive stable observations are
            needed before the state counts as settled.
        settle_budget: how long to wait for the session to come back **on
            its own** after the process is back. ``None`` uses the default
            (a share of the deadline); ``0`` skips the wait, for a program
            whose session never returns without an explicit reconnect.
        dry_run: report the plan without acting on anything.
        sleep: injectable for tests.

    Returns:
        A :class:`RecoveryReport`. ``ok`` is True only when the session
        was **observed** back and stable; a delivered action that did not
        bring it back is not a success.
    """
    started = time.monotonic()
    stages: list[RecoveryStage] = []
    attempts = 0
    recovered = False

    def elapsed() -> float:
        return time.monotonic() - started

    def remaining() -> float:
        return max(0.0, deadline - elapsed())

    def probe(fn: Callable[[], bool]) -> bool:
        try:
            return bool(fn())
        except Exception:
            return False

    def poll(fn: Callable[[], bool], budget: float) -> bool:
        """Poll ``fn`` until it holds, the budget runs out, or it raises."""
        stop = time.monotonic() + max(0.0, budget)
        while True:
            if probe(fn):
                return True
            now = time.monotonic()
            if now >= stop:
                return False
            sleep(min(max(0.0, interval), max(0.0, stop - now)))

    def stage(name: str, action: Callable[[], Any], fallback: str) -> bool:
        """Run one remedy and record what it did."""
        nonlocal attempts
        attempts += 1
        started_at = elapsed()
        try:
            result = _call(action, remaining())
            ok = _truthy(result)
            detail = _detail_of(result, fallback)
        except Exception as exc:
            ok = False
            detail = f"{type(exc).__name__}: {exc}"
        stages.append(RecoveryStage(name, ok=ok,
                                     elapsed=elapsed() - started_at,
                                     detail=detail))
        return ok

    # ---------------------------------------------------------- detect
    if probe(session_up):
        return RecoveryReport(ok=True, recovered=True, stable=True,
                              elapsed=elapsed(), deadline=deadline,
                              attempts=attempts,
                              detail="session was already up; nothing to recover")

    stages.append(RecoveryStage("detect", ok=False, elapsed=elapsed(),
                                 detail="session is gone"))

    if dry_run:
        plan = []
        if ensure_process is not None:
            plan.append("start the process")
        if reconnect is not None:
            plan.append("reconnect")
        if stable is not None:
            plan.append("wait for a stable session")
        stages.append(RecoveryStage("plan", ok=True, elapsed=elapsed(),
                                     detail="would " + ", ".join(plan or ["wait"])))
        return RecoveryReport(ok=False, recovered=False, stable=False,
                              elapsed=elapsed(), deadline=deadline,
                              attempts=attempts, stages=stages, dry_run=True,
                              detail="dry-run: " + ", ".join(plan or ["wait"]))

    # --------------------------------------------------------- process
    if ensure_process is not None and remaining() > 0:
        process_ok = stage("process", ensure_process, "the process is back")
        if process_ok:
            # The session may come back on its own once the process is
            # there - poll for it before touching the session itself. A
            # program whose session never returns by itself says so with
            # ``settle_budget=0``, so the wait costs nothing.
            wait = (max(interval * 2, deadline * 0.4)
                    if settle_budget is None else max(0.0, settle_budget))
            budget = min(remaining(), wait)
            if poll(session_up, budget):
                recovered = True

    # -------------------------------------------------------- reconnect
    if not recovered and reconnect is not None and remaining() > 0:
        stage("reconnect", reconnect, "the reconnect was delivered")
        budget = min(remaining(), max(interval * 2, deadline * 0.4))
        if poll(session_up, budget):
            recovered = True

    if not recovered:
        return _finish(stages, started, deadline, attempts,
                       recovered=False, stable=False,
                       detail="the session did not come back in time",
                       error=f"the session was not back within {deadline:.1f}s")

    # ------------------------------------------------------------ stable
    stable_ok = True
    if stable is not None:
        started_at = elapsed()
        needed = max(1, int(stable_checks))
        settled = 0
        stop = time.monotonic() + remaining()
        while settled < needed:
            if probe(stable):
                settled += 1
            else:
                settled = 0
            if settled >= needed:
                break
            now = time.monotonic()
            if now >= stop:
                break
            sleep(min(max(0.0, interval), max(0.0, stop - now)))
        stable_ok = settled >= needed
        stages.append(RecoveryStage("stable", ok=stable_ok,
                                     elapsed=elapsed() - started_at,
                                     detail="the state held still" if stable_ok
                                     else "the state never settled"))

    return _finish(stages, started, deadline, attempts,
                   recovered=True, stable=stable_ok,
                   detail=("the session is back and settled" if stable_ok
                           else "the session is back but never settled"),
                   error=None if stable_ok else
                   "the session never reached a stable state")
