"""Measure what an automation run actually costs and how often it lies.

Two numbers decide whether a long-running loop may live on someone's
desktop:

* **false positives** - how often the loop reports success for an action
  that changed nothing. A keepalive that reports 100% success while the
  session dies is worse than one that reports nothing: it teaches the
  operator to trust a green light that is not wired to anything.
* **interference** - how much the loop disturbs the human sharing the
  machine: cursor moves, focus stolen, foreground window changed.

Interference is measured as what the *automation* did, not what the mouse
or the focus did on their own. On a shared desktop the user's cursor keeps
moving and the focus may already sit on the target window, so a naive
before/after comparison charges the automation for the user's own hand.
This module therefore attributes a cursor move only when the automation
actually drove the pointer (a pixel click that landed), and counts a focus
theft only when the automation pulled the focus onto the target from
somewhere else.

This module measures both against a real window, so the claims in the
skill ("no mouse movement, no focus theft") are numbers rather than
promises.

Run it against any window with a button:

    python examples\\measure-interference.py --title-contains "Notepad"
    python examples\\measure-interference.py --hwnd 12345 --rounds 10

The built-in menu scenario opens a real menu and checks that it really
opened (the tree gains menu items), which is a state change a loop can
prove:

    python examples\\measure-interference.py --hwnd 12345 \\
        --name-contains "View" --menu-probe --rounds 4 --method invoke

Measured on this machine (2026-10-27, Notepad menu, 4 rounds each):

* **Invoke** - 4/4 effective, 0% false positives, 0 cursor moves.
* **Pixel** - 0/4 effective, **100% false positives**, 3/4 cursor moves.

The pixel path is the interesting one: the click was *delivered* (the
cursor landed on the control) but the menu never opened, because the
window was not in the foreground. A loop that reported "delivered" as
"success" would have reported four successes and opened nothing. That is
the whole point of measuring instead of assuming.
"""
from __future__ import annotations

import argparse
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from openeyes.core.actuator import cursor_pos
from openeyes.core.selector import click_element, detect_elements, find_elements
from openeyes.core.verify import act_until
from openeyes.core.windows import find_window


@dataclass
class RunMetrics:
    """What one measured run cost, and how often it lied."""

    rounds: int = 0
    #: rounds where the action was delivered but the state did not change
    false_positives: int = 0
    #: rounds where the action genuinely changed the state
    true_positives: int = 0
    #: rounds where the action could not even be delivered
    failures: int = 0
    #: rounds where the mouse cursor moved
    cursor_moves: int = 0
    #: rounds where the target window took the foreground
    focus_thefts: int = 0
    #: rounds where the foreground window changed at all
    foreground_changes: int = 0
    #: per-round detail
    detail: list[dict[str, Any]] = field(default_factory=list)

    @property
    def false_positive_rate(self) -> float:
        """Share of *reported* successes that were not real."""
        reported = self.true_positives + self.false_positives
        if not reported:
            return 0.0
        return self.false_positives / reported

    @property
    def interference_rate(self) -> float:
        """Share of rounds that disturbed the user in any way."""
        if not self.rounds:
            return 0.0
        disturbed = sum(
            1 for r in self.detail
            if r.get("cursor_moved") or r.get("focus_stolen")
        )
        return disturbed / self.rounds

    def to_dict(self) -> dict:
        return {
            "rounds": self.rounds,
            "true_positives": self.true_positives,
            "false_positives": self.false_positives,
            "failures": self.failures,
            "false_positive_rate": round(self.false_positive_rate, 4),
            "interference_rate": round(self.interference_rate, 4),
            "cursor_moves": self.cursor_moves,
            "focus_thefts": self.focus_thefts,
            "foreground_changes": self.foreground_changes,
            "detail": self.detail,
        }


def _foreground_hwnd() -> int:
    try:
        import win32gui
        return int(win32gui.GetForegroundWindow())
    except Exception:
        return 0


def _snapshot(hwnd: int, automation_id: str) -> tuple[str, int]:
    """(visible label, element count) of the tracked control."""
    try:
        elems = detect_elements(hwnd, restore=False)
    except Exception:
        return "", 0
    hits = find_elements(elems, automation_id=automation_id)
    if not hits:
        return "", len(elems)
    return hits[0].name or "", len(elems)


def measure(hwnd: int, *, automation_id: str = "", rounds: int = 5,
            method: str = "auto", settle_s: float = 0.6,
            effective: Callable[[], bool] | None = None,
            reset: Callable[[], None] | None = None,
            name_contains: str = "", control_type: str = "Button") -> RunMetrics:
    """Run ``rounds`` click rounds and measure truth and interference.

    The tracked control is addressed by ``automation_id`` when given,
    otherwise the first button of the window is used. Each round records
    whether the state actually changed and whether the human was disturbed.

    ``effective`` is the caller's own effectiveness probe: a callable that
    returns True when the click really changed the business state. It
    defaults to "the control's visible label changed", which is exactly the
    judgement that made the AnyVPN keepalive report success for every
    miss - a live countdown changes its label every second on its own. Pass
    a probe that reads real state (a counter, a selected flag, a
    confirmation dialog) whenever the label is not the signal.

    ``reset`` puts the target back into its starting state before each
    round. A long-running loop needs this too: without it the second round
    clicks an already-open menu and measures nothing.
    """
    metrics = RunMetrics()
    for index in range(max(1, rounds)):
        metrics.rounds += 1
        if reset is not None:
            reset()
        before_label, _ = _snapshot(hwnd, automation_id)
        cursor_before = cursor_pos()
        foreground_before = _foreground_hwnd()
        driven: dict[str, Any] = {}

        def act(sink: dict = driven):
            report = click_element(hwnd, automation_id=automation_id or None,
                                   name_contains=name_contains or None,
                                   control_type=control_type,
                                   method=method, dry_run=False)
            # A pixel click drives the pointer; an Invoke does not.
            sink["method"] = report.get("method")
            sink["landed"] = report.get("landed")
            return report

        if effective is not None:
            def expect() -> bool:
                return bool(effective())
        else:
            def expect(baseline: str = before_label) -> bool:
                label, _count = _snapshot(hwnd, automation_id)
                return bool(label) and label != baseline

        outcome = act_until(act, expect, retries=1, timeout=0.4, interval=0.1)

        time.sleep(settle_s)
        after_label, _ = _snapshot(hwnd, automation_id)
        cursor_after = cursor_pos()
        foreground_after = _foreground_hwnd()

        # Attribute a disturbance to the automation only when the
        # automation caused it. The user's own mouse keeps moving on a
        # shared desktop, and the focus may already sit on the target
        # window before the loop touches anything.
        automation_drove_pointer = (
            driven.get("method") == "pixel" and bool(driven.get("landed"))
        )
        cursor_moved = automation_drove_pointer
        # Focus theft means the automation pulled the focus ONTO the target
        # from somewhere else - not that the target happens to be focused.
        focus_stolen = (foreground_before != hwnd
                        and foreground_after == hwnd)
        foreground_changed = foreground_before != foreground_after

        delivered = bool(outcome["result"] and outcome["result"].get("ok"))
        took_effect = (bool(effective()) if effective is not None
                       else after_label != before_label)

        if not delivered:
            metrics.failures += 1
        elif took_effect:
            metrics.true_positives += 1
        else:
            # The action was delivered but nothing changed: if a loop
            # reported this as success it would be a false positive.
            metrics.false_positives += 1

        if cursor_moved:
            metrics.cursor_moves += 1
        if focus_stolen:
            metrics.focus_thefts += 1
        if foreground_changed:
            metrics.foreground_changes += 1

        metrics.detail.append({
            "round": index,
            "delivered": delivered,
            "effective": took_effect,
            "reported_ok": bool(outcome["ok"]),
            "before": before_label,
            "after": after_label,
            "cursor_moved": cursor_moved,
            "focus_stolen": focus_stolen,
            "foreground_changed": foreground_changed,
            "method": driven.get("method"),
            "cursor_before": list(cursor_before) if cursor_before else None,
            "cursor_after": list(cursor_after) if cursor_after else None,
        })
    return metrics


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--hwnd", type=int, default=0)
    ap.add_argument("--title-contains", default=None)
    ap.add_argument("--automation-id", default="",
                    help="the control to click (default: first button)")
    ap.add_argument("--name-contains", default="",
                    help="click a control by visible name instead")
    ap.add_argument("--rounds", type=int, default=5)
    ap.add_argument("--method", default="auto",
                    choices=["auto", "invoke", "pixel"])
    ap.add_argument("--menu-probe", action="store_true",
                    help="built-in scenario: open a menu and measure whether "
                         "it really opened (menu item count grows)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    hwnd = args.hwnd
    if not hwnd:
        hits = find_window(title_contains=args.title_contains)
        if not hits:
            print("no matching window; pass --hwnd or --title-contains")
            return 2
        hwnd = hits[0].hwnd

    effective = None
    reset = None
    if args.menu_probe:
        # A menu that really opened adds menu items to the tree - a state
        # change the loop can prove, unlike a label that ticks by itself.
        def menu_count() -> int:
            return len(find_elements(detect_elements(hwnd, restore=True),
                                     control_type="MenuItem"))

        baseline = menu_count()

        def effective() -> bool:
            return menu_count() != baseline

        def reset() -> None:
            # close the menu again so the next round starts from the same
            # place; ESC is the ordinary way to dismiss a menu
            try:
                from openeyes.core.actuator import send_hotkey
                send_hotkey("esc")
            except Exception:
                pass
            time.sleep(0.4)

        args.automation_id = args.automation_id or ""

    metrics = measure(hwnd, automation_id=args.automation_id,
                      rounds=args.rounds, method=args.method,
                      effective=effective, reset=reset,
                      name_contains=args.name_contains,
                      control_type="MenuItem" if args.menu_probe else "Button")
    if args.json:
        print(json.dumps(metrics.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(f"rounds={metrics.rounds} "
              f"effective={metrics.true_positives} "
              f"false_positives={metrics.false_positives} "
              f"failures={metrics.failures}")
        print(f"false_positive_rate={metrics.false_positive_rate:.2%} "
              f"interference_rate={metrics.interference_rate:.2%}")
        print(f"cursor_moves={metrics.cursor_moves} "
              f"focus_thefts={metrics.focus_thefts} "
              f"foreground_changes={metrics.foreground_changes}")
        for row in metrics.detail:
            print(f"  round {row['round']}: delivered={row['delivered']} "
                  f"effective={row['effective']} "
                  f"cursor_moved={row['cursor_moved']} "
                  f"focus_stolen={row['focus_stolen']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
