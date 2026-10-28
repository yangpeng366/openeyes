"""Keepalive: renew a session before it lapses.

The template for a supervisor whose target has a **countdown** - a free
session, a token that expires, a connection that dies on a timer.

The old way to write this was a fixed timer, and it always lost: a
20-second poll against a 20-second session clicks exactly at the expiry,
the instant the session dies. This template reads the countdown instead
and renews while there is still margin left.

What to fill in for an application:

* ``read_countdown`` - the seconds remaining, from the control's label;
* ``renew``          - the action that extends the session;
* ``is_renewed``     - the proof, read from business state.

Run it:

    python examples\\templates\\keepalive.py --title-contains "Any VPN" \\\\
        --automation-id KeepFreeAliveButton --countdown-regex "(\\d+):(\\d+)\\s*left" \\\\
        --trigger 10 --rounds 5

The default is dry-run; pass ``--go`` to actually click.
"""
from __future__ import annotations

import argparse
import json
import re
import sys

from openeyes.core.selector import click_element, detect_elements, find_elements
from openeyes.core.supervise import Health, supervise
from openeyes.core.windows import find_window


def parse_countdown(label: str | None) -> int | None:
    """Seconds remaining from a label like "0:18 left - click to reset"."""
    match = re.search(r"(\d+):(\d+)", label or "")
    if not match:
        return None
    return int(match.group(1)) * 60 + int(match.group(2))


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--title-contains", required=True)
    ap.add_argument("--automation-id", required=True,
                    help="the stable id of the renewal control")
    ap.add_argument("--countdown-regex", default=r"(\d+):(\d+)\s*left",
                    help="how to read the remaining seconds out of the label")
    ap.add_argument("--trigger", type=int, default=10,
                    help="renew while this many seconds remain (default 10)")
    ap.add_argument("--rounds", type=int, default=5,
                    help="0 = run forever")
    ap.add_argument("--interval", type=float, default=20.0,
                    help="fallback gap when the state carries no countdown")
    ap.add_argument("--go", action="store_true",
                    help="actually renew (default is dry-run)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    pattern = re.compile(args.countdown_regex, re.IGNORECASE)
    hits = find_window(title_contains=args.title_contains)
    if not hits:
        print(f"no window matching {args.title_contains!r}", file=sys.stderr)
        return 2
    hwnd = hits[0].hwnd

    def remaining() -> int | None:
        """Seconds left, read from the live control."""
        try:
            elements = detect_elements(hwnd, restore=True)
        except Exception:
            return None
        found = find_elements(elements, automation_id=args.automation_id)
        if not found:
            return None
        label = found[0].name or ""
        match = pattern.search(label)
        if not match:
            return None
        return int(match.group(1)) * 60 + int(match.group(2))

    def health() -> Health:
        left = remaining()
        if left is None:
            return Health(ok=False, due_in=None,
                          detail="the renewal control is not visible")
        if left > args.trigger:
            return Health(ok=True, due_in=float(left - args.trigger),
                          detail=f"{left}s left",
                          state={"countdown": left})
        return Health(ok=False, due_in=0.0, detail=f"{left}s left - renew now",
                      state={"countdown": left})

    def act() -> dict:
        report = click_element(hwnd, automation_id=args.automation_id,
                               method="auto", dry_run=not args.go)
        return report

    def expect() -> bool:
        """The proof: the countdown went back up, never "the label changed"."""
        left = remaining()
        return left is not None and left > args.trigger

    report = supervise(health=health, act=act, expect=expect,
                       interval=args.interval, max_rounds=args.rounds,
                       dry_run=not args.go)
    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(f"rounds={report.rounds} healthy={report.healthy} "
              f"actions={report.actions} effective={report.effective} "
              f"misses={report.misses}")
        print(f"false_positive_rate={report.false_positive_rate:.2%} "
              f"interference_rate={report.interference_rate:.2%}")
        for row in report.timeline:
            print(f"  round {row.index}: healthy={row.healthy} "
                  f"acted={row.acted} effective={row.effective} "
                  f"{row.detail}")
    return 0 if report.misses == 0 and report.failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
