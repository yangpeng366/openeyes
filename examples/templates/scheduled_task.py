"""Scheduled task: do the thing when it is due, and prove it happened.

The template for work that happens on a schedule: publish a report,
refresh a dashboard, submit a form, send a digest.

The trap here is different from the other two. A keepalive polls a
countdown and a login polls a presence, but a scheduled task has **no
state in the application** to read - the question "is it time yet?" is
answered by the clock, and the question "did it work?" is answered by
*the task's own result*.

So this template separates the two clocks deliberately:

* **due** - when the next run should happen, kept by the supervisor;
* **done** - whether the last run actually accomplished something, read
  from the task's own output, not from "the command returned".

A scheduled task that only knows "I called it" will report success for
every run, including the ones that silently did nothing.

Run it:

    python examples\\templates\\scheduled_task.py --title-contains "Report" \\\\
        --every 300 --rounds 3 --command "echo done"

The default is dry-run: the command is not executed, only described.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time
from typing import Any

from openeyes.core.supervise import Health, supervise


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--command", required=True,
                    help="the command to run when the task is due")
    ap.add_argument("--every", type=float, default=300.0,
                    help="seconds between runs (default 300)")
    ap.add_argument("--title-contains", default=None,
                    help="a window to bring up before running, if any")
    ap.add_argument("--rounds", type=int, default=3, help="0 = run forever")
    ap.add_argument("--go", action="store_true",
                    help="actually run the command (default is dry-run)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    # The schedule is state, not a fixed sleep: the supervisor reads the
    # remaining time from here and schedules the next round from it.
    state: dict[str, Any] = {"next_due": time.monotonic() + args.every,
                             "last": None}

    def health() -> Health:
        remaining = state["next_due"] - time.monotonic()
        if remaining > 0:
            return Health(ok=True, due_in=remaining,
                          detail=f"due in {remaining:.0f}s")
        return Health(ok=False, due_in=0.0, detail="due now")

    def act() -> dict:
        """Run the command, and report what it actually did."""
        if not args.go:
            return {"ok": True, "method": "dry-run", "command": args.command}
        proc = subprocess.run(args.command, shell=True, capture_output=True,
                              text=True, timeout=300)
        return {
            "ok": proc.returncode == 0,
            "method": "command",
            "returncode": proc.returncode,
            "stdout": (proc.stdout or "")[-2000:],
            "stderr": (proc.stderr or "")[-2000:],
            "command": args.command,
        }

    def act_and_check() -> dict:
        report = act()
        state["last"] = report
        # The next run is scheduled from now, not from a fixed cadence:
        # a run that took a long time does not immediately queue another.
        state["next_due"] = time.monotonic() + args.every
        return report

    def expect_checked() -> bool:
        report = state.get("last")
        if not report:
            return False
        if report.get("method") == "dry-run":
            return True
        if not report.get("ok"):
            return False
        return bool((report.get("stdout") or "").strip())

    report = supervise(health=health, act=act_and_check, expect=expect_checked,
                       interval=args.every, max_rounds=args.rounds,
                       dry_run=not args.go)

    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(f"rounds={report.rounds} healthy={report.healthy} "
              f"actions={report.actions} effective={report.effective} "
              f"misses={report.misses}")
        for row in report.timeline:
            print(f"  round {row.index}: due={not row.healthy} "
                  f"acted={row.acted} effective={row.effective} {row.detail}")
    return 0 if report.misses == 0 and report.failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
