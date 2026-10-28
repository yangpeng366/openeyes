"""Login: sign in again when the session expired.

The template for a supervisor whose target is **absent until it is
signed into**: a dashboard that shows a login form, an editor that shows
a sign-in wall, a client that quietly drops its session.

The shape is the same as keepalive - read the state, act when it is
wrong, prove the act took effect - but two things differ:

* the state is usually a **presence** ("the login form is there") rather
  than a countdown;
* the proof must be the *absence* of the login form, not the presence of
  something else. A click that is delivered while the form stays on
  screen is a miss, and the loop must say so rather than report success.

That second point is where naive logins go wrong: "the button was
clicked" feels like success, and the loop then sits there believing it
is signed in.

Run it:

    python examples\\templates\\login.py --title-contains "Some App" \\\\
        --login-automation-id LoginButton --signed-in-automation-id UserMenu \\\\
        --rounds 3

The default is dry-run; pass ``--go`` to actually click.
"""
from __future__ import annotations

import argparse
import json
import sys

from openeyes.core.selector import click_element, detect_elements, find_elements
from openeyes.core.supervise import Health, supervise
from openeyes.core.windows import find_window


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--title-contains", required=True)
    ap.add_argument("--login-automation-id", required=True,
                    help="the control that starts the sign-in")
    ap.add_argument("--signed-in-automation-id", default=None,
                    help="a control that only exists once signed in")
    ap.add_argument("--signed-out-automation-id", default=None,
                    help="a control that only exists while signed out")
    ap.add_argument("--rounds", type=int, default=3, help="0 = run forever")
    ap.add_argument("--interval", type=float, default=30.0)
    ap.add_argument("--go", action="store_true",
                    help="actually click (default is dry-run)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if not args.signed_in_automation_id and not args.signed_out_automation_id:
        print("need --signed-in-automation-id or --signed-out-automation-id: "
              "without one there is no proof that the sign-in happened",
              file=sys.stderr)
        return 2

    hits = find_window(title_contains=args.title_contains)
    if not hits:
        print(f"no window matching {args.title_contains!r}", file=sys.stderr)
        return 2
    hwnd = hits[0].hwnd

    def has_control(automation_id: str | None) -> bool:
        if not automation_id:
            return False
        try:
            elements = detect_elements(hwnd, restore=True)
        except Exception:
            return False
        return bool(find_elements(elements, automation_id=automation_id))

    def signed_in() -> bool:
        """The state: is the session good right now?"""
        if args.signed_in_automation_id and has_control(args.signed_in_automation_id):
            return True
        return bool(args.signed_out_automation_id
                    and not has_control(args.signed_out_automation_id))

    def health() -> Health:
        good = signed_in()
        return Health(ok=good, due_in=None,
                      detail="signed in" if good else "signed out - sign in",
                      state={"signed_in": good})

    def act() -> dict:
        return click_element(hwnd, automation_id=args.login_automation_id,
                             method="auto", dry_run=not args.go)

    def expect() -> bool:
        """The proof: the login form is gone (or the signed-in control is there)."""
        return signed_in()

    report = supervise(health=health, act=act, expect=expect,
                       interval=args.interval, max_rounds=args.rounds,
                       dry_run=not args.go)
    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(f"rounds={report.rounds} healthy={report.healthy} "
              f"actions={report.actions} effective={report.effective} "
              f"misses={report.misses}")
        for row in report.timeline:
            print(f"  round {row.index}: healthy={row.healthy} "
                  f"acted={row.acted} effective={row.effective} {row.detail}")
    return 0 if report.misses == 0 and report.failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
