"""OpenEyes showcase — keep Any VPN's free session alive.

Default behavior is DRY-RUN. Pass --go to actually click.

Usage:
    # dry-run (no clicks): print what would be clicked
    python examples\anyvpn_keepalive.py

    # real keepalive loop
    python examples\anyvpn_keepalive.py --go --bootstrap

Why the previous version kept missing ("点了没反应 / 续不上")
----------------------------------------------------------
1. *The name of the keepalive button is a live countdown* —
   "0:18 left - click to reset" changes every second on its own. The old
   check "did the button name change?" therefore reported a hit even when
   the click had done nothing at all. Success is now judged by the
   countdown jumping back up, never by the label text.
2. *The fixed 20s poll raced the expiry.* The free session lives 20s after
   the last click, so a 20s interval always clicked at exactly 0:00 — the
   instant the session dies. The loop is now countdown-driven: it renews
   while the button still has --trigger seconds (default 10) left.
3. *Pixel clicks are racy.* A single SetCursorPos + mouse_event can be
   delivered at the *previous* cursor position, because the target window
   has not processed WM_MOUSEMOVE yet. Clicks now prefer the UIA Invoke
   pattern (no pixel maths, no focus, and the user's mouse is never
   moved); the pixel path is only a fallback and now moves the cursor in
   steps with a settle pause.
4. *Recovery was missing.* When the free session lapses, Any VPN drops the
   session and closes its window. With --bootstrap the script now relaunches
   the app and reconnects instead of leaving the user offline. That recovery
   is the generic `openeyes.core.recover` primitive, it runs inside one
   budget (20s), and it no longer happens in the health probe: a read that
   acts cannot report what it did - and a dry run used to launch the app.
"""
from __future__ import annotations

import argparse
import re
import time
from pathlib import Path
from typing import NamedTuple

from openeyes import click_element, detect_elements, ensure_dpi_awareness, find_elements
from openeyes.core.lifecycle import app_state, ensure_app, restore_window
from openeyes.core.recover import RecoveryReport, recover_session
from openeyes.core.supervise import Health, Round, supervise
from openeyes.core.verify import act_until

# Coordinates must be physical screen pixels, otherwise the click lands
# a few pixels off (or on a different element) on scaled monitors.
ensure_dpi_awareness()

import win32con  # noqa: E402
import win32gui  # noqa: E402

# --- stable anchors (visible names change every second) ---------------
RESET_AID = "KeepFreeAliveButton"   # "0:18 left - click to reset"
CONNECT_AID = "ConnectButton"       # "Secure my connection"
MINIMIZE_AID = "Minimize"           # title bar '-'
RESET_NAME_RE = r"click\s*to\s*reset"

# The renewal target is a single stable control id. It must never resolve
# to the connect button: clicking that changes network routing, which is
# out of scope for a keepalive loop (reconnecting stays opt-in).
RENEWAL_AUTOMATION_IDS = (RESET_AID,)

# Microsoft Store app id — used to relaunch Any VPN after the free
# session lapses and the app closes its window.
APP_AUMID = r"shell:AppsFolder\AnywaySoftInc.AnyVPN_0qkrc2qacwvfm!App"
APP_AUMID_ID = r"AnywaySoftInc.AnyVPN_0qkrc2qacwvfm!App"

# How to recognise the Any VPN window and its process. The app is a
# Microsoft Store (MSIX) package, so the frame window's class is
# ApplicationFrameWindow.
WINDOW_TITLE = "Any VPN"
WINDOW_CLASS = "ApplicationFrameWindow"
PROCESS_NAME = "Any VPN.exe"

COUNTDOWN_RE = re.compile(r"(\d+):(\d+)\s*left", re.IGNORECASE)

SW_SHOWNOACTIVATE = 4  # restore without stealing the user's focus

#: The recovery budget: a dropped session must be back within 20 seconds
#: (dropped -> launch -> reconnect -> stable). The number is measured and
#: reported, never hoped for.
RECOVERY_DEADLINE = 20.0

#: Any VPN's session does not come back on its own when the app is
#: relaunched - it opens with the session down, waiting for a click. So
#: the "did it come back by itself?" wait is short.
RECOVERY_SETTLE = 2.0


# ---------------------------------------------------------------- helpers
def parse_countdown(label: str | None) -> int | None:
    """Seconds left parsed from '0:18 left - click to reset' (None if absent)."""
    m = COUNTDOWN_RE.search(label or "")
    if not m:
        return None
    return int(m.group(1)) * 60 + int(m.group(2))


def should_renew(countdown: int | None, trigger: int) -> bool:
    """True when the countdown has fallen into the renewal window."""
    return countdown is not None and countdown <= trigger


def judge_renewal(before: int | None, after: int | None) -> str:
    """Verdict for one renewal attempt.

    ``hit``    — the countdown jumped back up, the session was renewed.
    ``miss``   — the countdown kept falling, the click did nothing.
    ``gone``   — the keepalive button disappeared, the session lapsed.
    ``unknown``— the button carries no countdown, success cannot be judged.
    """
    if after is None:
        return "gone"
    if before is None:
        return "unknown"
    return "hit" if after > before else "miss"


def seconds_until_due(countdown: int | None, trigger: int) -> float:
    """How long to wait before the countdown enters the renewal window."""
    if countdown is None:
        return 0.0
    return max(0.0, float(countdown) - float(trigger))


def should_connect(*, has_keepalive_button: bool, bootstrap: bool) -> bool:
    """Whether the loop may click 'Secure my connection'.

    Reconnecting changes network routing, so it is opt-in (``--bootstrap``)
    and never happens while a keepalive button is present.
    """
    return (not has_keepalive_button) and bootstrap


class SessionState(NamedTuple):
    """What one read of Any VPN shows - and nothing else.

    A read never acts: it reports whether the app is up, whether the
    free session is up, and how much of it is left.
    """

    app_up: bool
    hwnd: int | None = None
    session_up: bool = False
    countdown: int | None = None
    detail: str = ""


def read_session_state() -> SessionState:
    """Read the state of Any VPN. A pure read: it never acts on it.

    The one thing a read may do is un-minimise the window
    (SW_SHOWNOACTIVATE - no focus theft, no cursor move). A minimised
    UWP window has an empty accessibility tree, so without that restore
    the loop would mistake a hidden window for a dead session - and a
    reconnect on a live session would kill it.
    """
    hwnd = find_anyvpn_window()
    if hwnd is None:
        return SessionState(app_up=False, detail="Any VPN is not running")
    show_without_focus(hwnd)
    label, countdown = read_keepalive(hwnd)
    if label is None:
        return SessionState(app_up=True, hwnd=hwnd, session_up=False,
                            detail="the app is up but the session is down")
    return SessionState(app_up=True, hwnd=hwnd, session_up=True,
                        countdown=countdown,
                        detail=f"timer {countdown}s left")


def decide_health(state: SessionState, *, trigger: int,
                  interval: float) -> Health:
    """Turn a state into a verdict. A pure function - it cannot act.

    The old version relaunched the app and clicked "Secure my
    connection" from here, which meant a dry run really launched Any
    VPN. A health probe that acts cannot report what it did.
    """
    if not state.app_up:
        return Health(ok=False, due_in=0.0, detail="Any VPN is not running")
    if not state.session_up:
        return Health(ok=False, due_in=0.0, detail="the session is down")
    countdown = state.countdown
    if countdown is None:
        # The button is there but carries no countdown: there is no state
        # to schedule from, so fall back to the plain interval.
        return Health(ok=True, due_in=interval,
                      detail="the keepalive button carries no countdown")
    if should_renew(countdown, trigger):
        return Health(ok=False, due_in=0.0,
                      detail=f"timer {countdown}s left - renew now",
                      state={"countdown": countdown})
    return Health(ok=True, due_in=seconds_until_due(countdown, trigger),
                  detail=f"timer {countdown}s left",
                  state={"countdown": countdown})


def find_anyvpn_window() -> int | None:
    """hwnd of the Any VPN frame window, or None when the app is not up.

    The lookup itself lives in openeyes.core.lifecycle: the app-state
    tier separates "the process is gone" from "the window is gone",
    which is exactly the distinction a keepalive loop has to make.
    """
    state = app_state(title_contains=WINDOW_TITLE,
                      class_name=WINDOW_CLASS,
                      process_name=PROCESS_NAME)
    return state.hwnd or None


def show_without_focus(hwnd: int) -> None:
    """Bring a minimised or hidden window back without activating it.

    SW_SHOWNOACTIVATE repopulates the UIA tree (a minimised UWP window has
    an empty one) while leaving the user's foreground window and keyboard
    focus untouched.
    """
    restore_window(hwnd)


def hide_window(hwnd: int) -> bool:
    """Minimise the Any VPN window so it stays out of the user's way."""
    try:
        if win32gui.IsIconic(hwnd):
            return True
        win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
        time.sleep(0.2)
        return bool(win32gui.IsIconic(hwnd))
    except Exception:
        return False


def relaunch_app(*, dry_run: bool = False, timeout: float = 20.0) -> bool:
    """Start Any VPN again (the free session closing takes the window with it).

    ``timeout`` is the caller's budget: the recovery tells us how much
    time is left, so the launch cannot overrun the promise.
    """
    result = ensure_app(title_contains=WINDOW_TITLE,
                        class_name=WINDOW_CLASS,
                        process_name=PROCESS_NAME,
                        aumid=APP_AUMID_ID,
                        timeout=timeout,
                        dry_run=dry_run)
    return bool(result.ok)


def read_keepalive(hwnd: int):
    """(label, countdown) of the keepalive button, or (None, None)."""
    try:
        elems = detect_elements(hwnd)
    except Exception:
        return None, None
    hit = find_elements(elems, automation_id=RESET_AID)
    if not hit:
        hit = find_elements(elems, regex=RESET_NAME_RE, control_type="Button")
    if not hit:
        return None, None
    label = hit[0].name or ""
    return label, parse_countdown(label)



def _shoot(out_dir: Path | None, hwnd: int, label: str) -> None:
    """Save a screenshot of the window. Best-effort, never raises."""
    if out_dir is None:
        return
    try:
        from openeyes import capture_window
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        capture_window(hwnd).save(out_dir / f"anyvpn_{stamp}_{label}.png", "PNG")
    except Exception:
        pass


def renew(hwnd: int, *, dry_run: bool, retries: int,
          out_dir: Path | None, iteration: int) -> tuple[str, str]:
    """Renew the free session. Returns (verdict, detail).

    This is the reference implementation of the act-then-prove pattern:
    click, then wait until the countdown actually jumped back up. A
    delivered click that changed nothing is a miss, never a success.
    """
    label, before = read_keepalive(hwnd)
    state = {"method": "auto", "last": ""}

    def act():
        report = click_element(hwnd, automation_id=RESET_AID,
                               control_type="Button", method=state["method"],
                               dry_run=dry_run)
        if not report.get("ok"):
            # automation id may be missing on other builds — retry by name
            report = click_element(hwnd, name_regex=RESET_NAME_RE,
                                   control_type="Button", method=state["method"],
                                   dry_run=dry_run)
        state["last"] = (f"method={report.get('method')} "
                         f"name={report.get('name')!r} "
                         f"center={report.get('center')} err={report.get('error')}")
        return report

    def expect() -> bool:
        _, after = read_keepalive(hwnd)
        return judge_renewal(before, after) == "hit"

    def on_retry(attempt: int, _result) -> None:
        print(f"[openeyes] iter {iteration}: renewal MISS "
              f"(timer {before}s), retry {attempt}/{retries}", flush=True)
        # the other method next time round
        state["method"] = "pixel" if state["method"] != "pixel" else "invoke"

    if dry_run:
        act()
        return "dry-run", state["last"]

    outcome = act_until(act, expect, retries=retries, timeout=1.2,
                        interval=0.2, on_retry=on_retry)
    _, after = read_keepalive(hwnd)
    verdict = judge_renewal(before, after)
    detail = (f"{state['last']} timer {before}s -> {after}s "
              f"(attempts={outcome['attempts']})")
    return verdict, detail


def renew_for_supervisor(hwnd: int, *, dry_run: bool, retries: int,
                          out_dir: Path | None, iteration: int) -> dict:
    """Renew once and report it in the shape the supervisor expects.

    The domain verdict (hit / miss / gone / unknown) travels with the
    report, so the loop can say *why* a round failed instead of only
    that it did.
    """
    verdict, detail = renew(hwnd, dry_run=dry_run, retries=retries,
                            out_dir=out_dir, iteration=iteration)
    return {"ok": verdict in ("hit", "dry-run", "unknown"),
            "method": "invoke", "verdict": verdict, "detail": detail}


def connect(hwnd: int, *, dry_run: bool) -> tuple[bool, str]:
    """Click 'Secure my connection' so the free session starts again.

    The very same button carries the label 'Disconnect' while a session is
    up. Clicking it then would kill a live session, so the label is read
    first and the click is refused unless it really says 'Secure my
    connection'.
    """
    try:
        elems = detect_elements(hwnd)
        hit = find_elements(elems, automation_id=CONNECT_AID)
    except Exception:
        hit = []
    if hit:
        current = (hit[0].name or "").strip()
        if "disconnect" in current.lower():
            return False, (f"refused to click: the button says {current!r} "
                           f"(a session is already up)")
    report = click_element(hwnd, automation_id=CONNECT_AID, control_type="Button",
                           method="auto", dry_run=dry_run)
    if not report.get("ok"):
        report = click_element(hwnd, name_contains="Secure my connection",
                               control_type="Button", method="auto", dry_run=dry_run)
    return bool(report.get("ok")), (f"method={report.get('method')} "
                                    f"name={report.get('name')!r} "
                                    f"err={report.get('error')}")


def recover_anyvpn(*, dry_run: bool, deadline: float = RECOVERY_DEADLINE,
                   interval: float = 1.0,
                   settle: float = RECOVERY_SETTLE) -> RecoveryReport:
    """Bring a dropped session back inside one budget.

    The chain is the one the acceptance numbers are written against:
    **dropped -> launch -> reconnect -> renew**. The first three steps
    live here; the renewal is the supervisor's next round, because a
    fresh session carries a fresh countdown and is renewed while it
    still has margin.

    The report carries the number: ``recovery_seconds`` is how long the
    chase took, ``deadline`` is the budget it was held to. ``settle`` is
    how long to wait for a session that returns by itself - Any VPN's
    does not, so the wait is short.
    """
    def session_up() -> bool:
        return read_session_state().session_up

    def ensure_process(budget: float = RECOVERY_DEADLINE) -> dict:
        ok = relaunch_app(dry_run=dry_run, timeout=max(1.0, budget))
        return {"ok": ok,
                "detail": "Any VPN is back" if ok
                else "Any VPN could not be relaunched"}

    def reconnect() -> dict:
        hwnd = find_anyvpn_window()
        if hwnd is None:
            return {"ok": False, "detail": "Any VPN is not running"}
        ok, detail = connect(hwnd, dry_run=dry_run)
        return {"ok": ok, "detail": detail}

    def stable() -> bool:
        """The countdown is alive - a session halfway up is not a session."""
        state = read_session_state()
        return state.session_up and (state.countdown or 0) > 0

    return recover_session(session_up=session_up,
                           ensure_process=ensure_process,
                           reconnect=reconnect,
                           stable=stable,
                           deadline=deadline,
                           interval=interval,
                           settle_budget=settle,
                           dry_run=dry_run)


# ------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--go", action="store_true",
                    help="actually click. Default is dry-run.")
    ap.add_argument("--interval", type=float, default=20.0,
                    help="seconds to wait before retrying while the session "
                         "is down (default 20)")
    ap.add_argument("--trigger", type=int, default=10,
                    help="renew while the countdown still has this many "
                         "seconds left (default 10)")
    ap.add_argument("--retries", type=int, default=3,
                    help="attempts per renewal, alternating invoke/pixel")
    ap.add_argument("--title-contains", default="Any VPN")
    ap.add_argument("--bootstrap", action="store_true",
                    help="allow reconnecting: click 'Secure my connection' and "
                         "relaunch the app when the free session lapses. "
                         "Changes network routing, so it stays opt-in.")
    ap.add_argument("--no-hide", dest="hide", action="store_false",
                    help="keep the Any VPN window visible instead of "
                         "minimising it after each renewal")
    ap.add_argument("--screenshot-dir", default=None,
                    help="if set, save before/after screenshots per click")
    ap.add_argument("--quiet", action="store_true",
                    help="only print renewals / errors, not idle rounds")
    ap.add_argument("--recovery-budget", type=float, default=RECOVERY_DEADLINE,
                    help="seconds allowed for bringing a dropped session back "
                         "(launch -> reconnect -> stable)")
    ap.add_argument("--max-iter", type=int, default=0, help="0 = run forever")
    args = ap.parse_args()

    out_dir = Path(args.screenshot_dir) if args.screenshot_dir else None
    stats = {"renewed": 0, "missed": 0, "rounds": 0, "unknown": 0, "failed": 0,
             "recoveries": 0, "recovered": 0, "recovery_failed": 0,
             "recovery_seconds": 0.0}
    t_start = time.time()

    print(f"[openeyes] keepalive: go={args.go} trigger={args.trigger}s "
          f"interval={args.interval}s bootstrap={args.bootstrap} "
          f"hide={args.hide} dpi={ensure_dpi_awareness()}", flush=True)

    # The renewal loop is the generic supervisor: read the countdown,
    # act when it is inside the renewal window, prove the countdown went
    # back up, and schedule the next round from the state that remains.
    # A fixed interval against a 20s session clicks exactly at 0:00.
    round_index = {"i": 0}

    def supervisor_health() -> Health:
        """Is the session healthy, and when is the next check due?

        A pure read. The old version relaunched the app and clicked
        "Secure my connection" from here, so a dry run really launched
        Any VPN - and a probe that acts cannot report what it did. The
        action belongs to the action.
        """
        state = read_session_state()
        health = decide_health(state, trigger=args.trigger,
                               interval=args.interval)
        if not args.quiet:
            print(f"[openeyes] iter {round_index['i']}: {health.detail}",
                  flush=True)
        return health

    def supervisor_act() -> dict:
        """The action: recover a dropped session, or renew a live one."""
        state = read_session_state()
        if not state.app_up or not state.session_up:
            # The session is gone: launch -> reconnect -> stable, inside
            # one budget. The renewal follows in the next round.
            if not should_connect(has_keepalive_button=state.session_up,
                                  bootstrap=args.bootstrap):
                return {"ok": False, "method": "none", "verdict": "failed",
                        "detail": "the session is down (--bootstrap not set, "
                                  "not reconnecting)"}
            report = recover_anyvpn(dry_run=not args.go,
                                    deadline=args.recovery_budget)
            stats["recoveries"] += 1
            stats["recovery_seconds"] = max(stats["recovery_seconds"],
                                             report.recovery_seconds)
            if report.ok:
                stats["recovered"] += 1
            else:
                stats["recovery_failed"] += 1
            return {"ok": report.ok, "method": "invoke",
                    "verdict": "recovered" if report.ok else "recovery-failed",
                    "detail": f"{report.detail} (recovery "
                              f"{report.recovery_seconds:.1f}s of "
                              f"{report.deadline:.0f}s)"}
        hwnd = state.hwnd
        if hwnd is None:
            return {"ok": False, "method": "none", "verdict": "failed",
                    "detail": "Any VPN is not running"}
        _shoot(out_dir, hwnd, f"before_renew{round_index['i']}")
        report = renew_for_supervisor(hwnd, dry_run=not args.go,
                                      retries=args.retries, out_dir=out_dir,
                                      iteration=round_index["i"])
        _shoot(out_dir, hwnd, f"after_renew{round_index['i']}")
        return report

    def supervisor_preview() -> dict:
        """What the action *would* report - a read-only probe.

        A dry run must not act, so the preview answers without clicking
        and without launching: it says what the run would do.
        """
        state = read_session_state()
        if not state.app_up:
            return {"ok": False, "method": "none", "verdict": "dry-run-recover",
                    "detail": "Any VPN is not running; a run would launch it, "
                              "reconnect and wait for the session"}
        if not state.session_up:
            return {"ok": False, "method": "none", "verdict": "dry-run-recover",
                    "detail": "the session is down; a run would reconnect and "
                              "wait for it to settle"}
        return {"ok": True, "method": "dry-run", "verdict": "dry-run",
                "detail": "Any VPN is up; the renewal would be attempted"}

    def supervisor_expect() -> bool:
        """The proof: the countdown went back up, never "the label changed"."""
        hwnd = find_anyvpn_window()
        if hwnd is None:
            return False
        _, after = read_keepalive(hwnd)
        return after is not None and after > args.trigger

    def supervisor_on_round(round_row: Round) -> None:
        verdict = round_row.verdict
        detail = round_row.detail
        if verdict == "hit":
            print(f"[openeyes] iter {round_row.index}: RENEWED  {detail}",
                  flush=True)
        elif verdict == "dry-run":
            print(f"[openeyes] iter {round_row.index}: [dry-run] would renew  "
                  f"{detail}", flush=True)
        elif verdict == "dry-run-recover":
            print(f"[openeyes] iter {round_row.index}: [dry-run] would recover  "
                  f"{detail}", flush=True)
        elif verdict == "recovered":
            print(f"[openeyes] iter {round_row.index}: RECOVERED  {detail}",
                  flush=True)
        elif verdict == "recovery-failed":
            print(f"[openeyes] iter {round_row.index}: RECOVERY FAILED  "
                  f"{detail}", flush=True)
        elif verdict == "gone":
            print(f"[openeyes] iter {round_row.index}: session lapsed during "
                  f"renewal ({detail})", flush=True)
        elif verdict == "failed":
            # the action could not be delivered (the app or the control
            # was gone) - that is a failure, not a missed renewal
            print(f"[openeyes] iter {round_row.index}: renewal not delivered "
                  f"({detail})", flush=True)
        elif verdict == "unknown":
            print(f"[openeyes] iter {round_row.index}: renewed (no countdown to "
                  f"verify) {detail}", flush=True)
        elif round_row.acted and verdict != "failed":
            print(f"[openeyes] iter {round_row.index}: RENEWAL FAILED  {detail}",
                  flush=True)
        if args.hide:
            # Keep the window out of the user's way. The next read restores
            # it without stealing focus when it needs to see the tree.
            hwnd = find_anyvpn_window()
            if hwnd is not None:
                hide_window(hwnd)
        round_index["i"] = round_row.index + 1

    report = None
    try:
        report = supervise(
            health=supervisor_health,
            act=supervisor_act,
            expect=supervisor_expect,
            interval=args.interval,
            max_rounds=args.max_iter,
            dry_run=not args.go,
            retries=args.retries,
            timeout=1.2,
            poll_interval=0.2,
            preview=supervisor_preview,
            on_round=supervisor_on_round,
        )
    except KeyboardInterrupt:
        print("[openeyes] interrupted by user")

    elapsed = time.time() - t_start
    if report is not None:
        # The supervisor report is the single source of truth for the
        # counts; the script only adds what it did on its own.
        stats["rounds"] = report.rounds
        stats["renewed"] = report.effective
        stats["missed"] = report.misses
        stats["failed"] = report.failures
    print(f"[openeyes] summary: rounds={stats['rounds']} renewed={stats['renewed']} "
          f"missed={stats['missed']} unknown={stats['unknown']} "
          f"failed={stats['failed']} "
          f"recoveries={stats['recoveries']} recovered={stats['recovered']} "
          f"recovery_failed={stats['recovery_failed']} "
          f"recovery_s={stats['recovery_seconds']:.1f} "
          f"elapsed={elapsed:.1f}s")
    if report is not None:
        # The trust numbers travel with every run: how often the loop
        # claimed a success that did not happen, and how often it
        # disturbed the person sharing the desktop.
        print(f"[openeyes] trust: false_positive_rate="
              f"{report.false_positive_rate:.2%} "
              f"interference_rate={report.interference_rate:.2%} "
              f"effective={report.effective} misses={report.misses} "
              f"failures={report.failures}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
