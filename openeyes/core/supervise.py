"""Supervision: the one loop shape behind keepalive, login and scheduled work.

Three jobs that look different are the same job:

* **keepalive** - renew the session before it lapses;
* **login**     - sign in again when the session expired;
* **scheduled** - do a task when its time has come.

Each one reads a *state*, decides from that state whether to act, acts,
then proves the action took effect. The old way to write them was a fixed
timer: a 20-second poll against a 20-second session clicks exactly at the
expiry - the instant the session dies - and leaves zero margin. A
supervisor that reads state instead schedules the next round from the
state, so it renews *while there is still time left*.

This module is that loop, once:

.. code-block:: python

    from openeyes.core.supervise import supervise

    report = supervise(
        health=lambda: Health(ok=countdown > 10, due_in=countdown - 10),
        act=lambda: click_element(hwnd, automation_id="KeepFreeAliveButton",
                                   method="invoke", dry_run=False),
        expect=lambda: countdown > before,
        max_rounds=5,
    )

Two rules make the numbers trustworthy:

* **A delivered action is not an effective action.** ``ok`` counts only
  rounds where the expected state was observed afterwards.
* **Interference is attributed to the automation.** The user's own cursor
  keeps moving on a shared desktop, so a cursor move counts only when the
  automation drove the pointer, and a focus theft only when it pulled the
  focus onto the target from somewhere else.
"""
from __future__ import annotations

import contextlib
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from openeyes.core.verify import act_until


@dataclass
class Health:
    """What the supervisor knows about the thing it watches.

    Attributes:
        ok: whether the target is healthy right now - no action needed.
        due_in: seconds until the next check is needed. ``None`` means
            "use the default interval". A state-driven supervisor sets it
            from the remaining time, so the next round lands *before* the
            deadline rather than on it.
        detail: a human-readable reason, kept for the audit trail.
        state: the raw facts the decision was made from.
    """

    ok: bool
    due_in: float | None = None
    detail: str = ""
    state: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"ok": self.ok, "due_in": self.due_in, "detail": self.detail,
                "state": self.state}


@dataclass
class Round:
    """One round of supervision, with everything that happened in it."""

    index: int
    healthy: bool
    acted: bool
    effective: bool
    due_in: float | None = None
    detail: str = ""
    error: str | None = None
    #: the caller's own verdict for the action ("hit", "gone", "unknown",
    #: ...), when it has one. The supervisor's ``effective`` is the
    #: generic judgement; this is the domain-specific one.
    verdict: str = ""
    interference: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "index": self.index, "healthy": self.healthy, "acted": self.acted,
            "effective": self.effective, "due_in": self.due_in,
            "detail": self.detail, "error": self.error, "verdict": self.verdict,
            "interference": self.interference,
        }


@dataclass
class SupervisionReport:
    """What a supervision run cost and how often it lied."""

    rounds: int = 0
    #: rounds that needed no action
    healthy: int = 0
    #: rounds where an action was attempted
    actions: int = 0
    #: rounds where the action was attempted and the state changed
    effective: int = 0
    #: rounds where the action was delivered but the state did not change
    misses: int = 0
    #: rounds where the action could not be delivered at all
    failures: int = 0
    #: rounds where the target was already healthy
    skipped: int = 0
    #: rounds where the automation drove the user's pointer
    physical_inputs: int = 0
    #: rounds where the automation pulled the focus onto the target
    focus_thefts: int = 0
    #: rounds where the foreground window changed at all - an ambient fact,
    #: not an accusation, because the user moves their own windows too
    foreground_changes: int = 0
    #: how many actions took each path ("invoke" / "pixel" / ...)
    methods: dict[str, int] = field(default_factory=dict)
    timeline: list[Round] = field(default_factory=list)

    @property
    def false_positive_rate(self) -> float:
        """Share of *reported* successes that were not real."""
        reported = self.effective + self.misses
        if not reported:
            return 0.0
        return self.misses / reported

    @property
    def interference_rate(self) -> float:
        """Share of rounds that disturbed the user."""
        if not self.rounds:
            return 0.0
        disturbed = sum(1 for r in self.timeline
                        if r.interference.get("disturbed"))
        return disturbed / self.rounds

    @property
    def physical_input_rate(self) -> float:
        """Share of *actions* that drove the user's pointer.

        ``interference_rate`` counts rounds; this counts actions, so it
        answers the question a caller actually asks: of the things we
        did, how many of them moved the mouse under the user's hand?
        """
        if not self.actions:
            return 0.0
        return self.physical_inputs / self.actions

    def to_dict(self) -> dict:
        return {
            "rounds": self.rounds,
            "healthy": self.healthy,
            "actions": self.actions,
            "effective": self.effective,
            "misses": self.misses,
            "failures": self.failures,
            "skipped": self.skipped,
            "false_positive_rate": round(self.false_positive_rate, 4),
            "interference_rate": round(self.interference_rate, 4),
            "physical_inputs": self.physical_inputs,
            "focus_thefts": self.focus_thefts,
            "foreground_changes": self.foreground_changes,
            "physical_input_rate": round(self.physical_input_rate, 4),
            "methods": dict(self.methods),
            "timeline": [r.to_dict() for r in self.timeline],
        }


#: How the caller's action reported what it did.
#: ``method`` is the actuation path ("invoke" / "pixel"), ``landed`` says
#: whether the pointer actually arrived.
ActionReport = dict[str, Any]


def _interference(before: dict[str, Any], after: dict[str, Any],
                  action_report: ActionReport | None) -> dict[str, Any]:
    """Attribute disturbance to the automation, not to the desktop.

    The user's own cursor keeps moving and the focus may already sit on
    the target, so a naive before/after comparison charges the automation
    for the user's own hand. A cursor move counts only when the
    automation drove the pointer (a pixel click that landed); a focus
    theft only when the automation pulled the focus onto the target from
    somewhere else.
    """
    report = action_report or {}
    method = report.get("method")
    landed = report.get("landed")
    automation_drove_pointer = (method == "pixel" and bool(landed))
    focus_before = before.get("foreground") or 0
    focus_after = after.get("foreground") or 0
    # The target is what the snapshot was taken around; the automation
    # stole the focus when the focus moved ONTO it from somewhere else.
    target = before.get("target") or 0
    focus_stolen = bool(target) and focus_before != target \
        and focus_after == target
    cursor_moved = automation_drove_pointer
    # An ambient fact, not an accusation: the focus moved somewhere during
    # the round. It may have been the user, not us.
    foreground_changed = bool(focus_before or focus_after) \
        and focus_before != focus_after
    return {
        "cursor_moved": cursor_moved,
        "focus_stolen": focus_stolen,
        "foreground_changed": foreground_changed,
        "method": method,
        "disturbed": bool(cursor_moved or focus_stolen),
    }


def _count_interference(report: SupervisionReport, row: Round,
                        attempted: bool) -> None:
    """Fold one round's disturbance facts into the run's totals.

    ``attempted`` says the automation really tried to act in this round.
    A round we sat out cannot have had its focus stolen by us, however
    the focus moved: the user moves their own windows too.
    """
    interference = row.interference or {}
    if interference.get("cursor_moved"):
        report.physical_inputs += 1
    if attempted and interference.get("focus_stolen"):
        report.focus_thefts += 1
    if interference.get("foreground_changed"):
        report.foreground_changes += 1
    method = interference.get("method")
    if method:
        report.methods[str(method)] = report.methods.get(str(method), 0) + 1


def _record_in_ledger(ledger: Any, row: Round,
                       action_report: ActionReport | None) -> None:
    """Write one round to a ledger, without letting the ledger break the loop.

    A receipt is a diagnostic, not a duty: if the ledger refuses the
    entry, the round still counts and the run still finishes.
    """
    report = action_report or {}
    method = report.get("method")
    interference = row.interference or {}
    with contextlib.suppress(Exception):
        # Imported inside the guard: a missing ledger must not stop the
        # renewal it was only meant to record.
        from openeyes.agent.ledger import delivery_for_method

        ledger.record(
            action=str(method or "none"),
            delivery=delivery_for_method(method),
            physical_input=bool(interference.get("cursor_moved")),
            focus_stolen=bool(interference.get("focus_stolen")),
            cursor_moved=bool(interference.get("cursor_moved")),
            verified=bool(row.effective),
            ok=bool(report.get("ok", True)),
            target=str(report.get("name") or report.get("automation_id") or ""),
            detail=str(row.detail or row.error or ""),
        )


def supervise(*, health: Callable[[], Health],
              act: Callable[[], Any],
              expect: Callable[[], bool],
              interval: float = 20.0,
              max_rounds: int = 0,
              dry_run: bool = False,
              retries: int = 3,
              timeout: float = 2.0,
              poll_interval: float = 0.2,
              snapshot: Callable[[], dict[str, Any]] | None = None,
              preview: Callable[[], Any] | None = None,
              on_round: Callable[[Round], None] | None = None,
              ledger: Any | None = None,
              sleep: Callable[[float], None] = time.sleep) -> SupervisionReport:
    """Watch a target, act when it is unhealthy, and prove the act worked.

    Each round:

    1. read the health;
    2. if it is healthy, do nothing and schedule the next round from the
       state (``Health.due_in``) or from ``interval``;
    3. otherwise act, then poll ``expect`` until the expected state is
       observed - a delivered action that changed nothing is a **miss**,
       not a success;
    4. schedule the next round from the state read *after* the action, so
       the next check lands before the deadline.

    ``dry_run=True`` never acts: the run reports what it *would* do. The
    default is False here because this primitive is the engine of a
    long-running loop; the CLI layer puts the dry-run default in front of
    it, where a human is the one deciding. ``max_rounds=0`` runs forever.

    ``snapshot`` may return the ambient facts the interference accounting
    needs (for example ``{"foreground": hwnd}``); without it the loop
    still runs and reports no interference.

    ``preview`` is a read-only probe used in dry-run mode to report what
    the action *would* say. The action itself is never called in a dry
    run - that is the whole promise of the mode.

    ``ledger`` is anything with a ``record(**fields)`` method - see
    ``openeyes.agent.ledger.ActionLedger``. When given, every round in
    which the automation really acted is written to it. The ledger must
    never break the loop: a failed receipt is swallowed, because losing
    a receipt may not cost a renewal.
    """
    report = SupervisionReport()
    index = 0
    while True:
        report.rounds += 1
        before = snapshot() if snapshot else {}
        state = health()
        action_report: ActionReport | None = None
        attempted = False
        round_row = Round(index=index, healthy=state.ok, acted=False,
                          effective=False, due_in=state.due_in,
                          detail=state.detail)

        if state.ok:
            report.healthy += 1
            report.skipped += 1
        else:
            report.actions += 1
            round_row.acted = True
            if dry_run:
                # A dry run must not act, so the action is never called.
                # A caller that wants to report what the action *would*
                # say supplies ``preview`` - a read-only probe.
                if preview is not None:
                    try:
                        peek = preview()
                    except Exception as exc:
                        peek = None
                        round_row.error = f"{type(exc).__name__}: {exc}"
                    if isinstance(peek, dict):
                        round_row.verdict = str(peek.get("verdict") or "")
                        if peek.get("ok") is False:
                            # A preview that reports the action would fail
                            # is still a failure the operator must see.
                            report.failures += 1
                            round_row.verdict = round_row.verdict or "failed"
                round_row.detail = (round_row.detail + "; dry-run"
                                    if round_row.detail else "dry-run")
            else:
                attempted = True
                outcome = act_until(act, expect, retries=retries,
                                    timeout=timeout, interval=poll_interval)
                action_report = outcome["result"] \
                    if isinstance(outcome["result"], dict) else None
                round_row.effective = bool(outcome["ok"])
                if isinstance(action_report, dict):
                    round_row.verdict = str(action_report.get("verdict") or "")
                if outcome["ok"]:
                    report.effective += 1
                elif action_report is not None and not action_report.get("ok"):
                    # The action itself failed - the target was gone, the
                    # control had moved. That is a failure to deliver, not a
                    # delivered action that changed nothing.
                    report.failures += 1
                    round_row.verdict = round_row.verdict or "failed"
                elif outcome["result"] is not None:
                    # the action was delivered but the state did not move
                    report.misses += 1
                else:
                    report.failures += 1
                round_row.error = outcome.get("error")
                after_state = health()
                round_row.due_in = after_state.due_in
                round_row.detail = after_state.detail or round_row.detail

        after = snapshot() if snapshot else {}
        round_row.interference = _interference(before, after, action_report)
        _count_interference(report, round_row, attempted)
        if ledger is not None and attempted:
            _record_in_ledger(ledger, round_row, action_report)
        report.timeline.append(round_row)
        if on_round is not None:
            on_round(round_row)

        index += 1
        if max_rounds and index >= max_rounds:
            break

        due = round_row.due_in
        wait = float(due) if due is not None else float(interval)
        if wait > 0:
            sleep(wait)
    return report


def supervise_window(*, health: Callable[[], Health],
                      act: Callable[[], Any],
                      expect: Callable[[], bool],
                      **kwargs: Any) -> SupervisionReport:
    """Convenience wrapper that records the ambient focus facts.

    The foreground window is read before and after each round so the
    interference accounting can tell "the automation stole the focus"
    from "the focus was already there".
    """
    from openeyes.core.actuator import cursor_pos

    target = int(kwargs.pop("target", 0) or 0)

    def snapshot() -> dict[str, Any]:
        focus = 0
        try:
            import win32gui
            focus = int(win32gui.GetForegroundWindow())
        except Exception:
            focus = 0
        return {"foreground": focus, "cursor": cursor_pos(), "target": target}

    kwargs.setdefault("snapshot", snapshot)
    return supervise(health=health, act=act, expect=expect, **kwargs)
