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
   the app and reconnects instead of leaving the user offline.
"""
from __future__ import annotations

import argparse
import re
import time
from pathlib import Path

from openeyes import click_element, detect_elements, ensure_dpi_awareness, find_elements
from openeyes.core.lifecycle import app_state, ensure_app, restore_window
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


def relaunch_app() -> bool:
    """Start Any VPN again (the free session closing takes the window with it)."""
    result = ensure_app(title_contains=WINDOW_TITLE,
                        class_name=WINDOW_CLASS,
                        process_name=PROCESS_NAME,
                        aumid=APP_AUMID_ID,
                        timeout=20.0,
                        dry_run=False)
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


def wait_for_keepalive(hwnd: int, timeout: float) -> tuple[str | None, int | None]:
    """Wait until the keepalive button shows up (the session came up)."""
    deadline = time.monotonic() + timeout
    while True:
        label, countdown = read_keepalive(hwnd)
        if label is not None or time.monotonic() >= deadline:
            return label, countdown
        time.sleep(0.5)


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
    ap.add_argument("--max-iter", type=int, default=0, help="0 = run forever")
    args = ap.parse_args()

    out_dir = Path(args.screenshot_dir) if args.screenshot_dir else None
    stats = {"renewed": 0, "missed": 0, "reconnects": 0, "relaunches": 0,
             "rounds": 0, "unknown": 0}
    t_start = time.time()
    i = 0
    next_due = time.monotonic()

    print(f"[openeyes] keepalive: go={args.go} trigger={args.trigger}s "
          f"interval={args.interval}s bootstrap={args.bootstrap} "
          f"hide={args.hide} dpi={ensure_dpi_awareness()}", flush=True)

    try:
        while True:
            stats["rounds"] += 1
            hwnd = find_anyvpn_window()
            if hwnd is None:
                if not args.bootstrap:
                    if not args.quiet:
                        print(f"[openeyes] iter {i}: Any VPN not running "
                              f"(--bootstrap not set, not launching it)",
                              flush=True)
                else:
                    print(f"[openeyes] iter {i}: Any VPN is not running -> "
                          f"relaunching", flush=True)
                    stats["relaunches"] += 1
                    relaunch_app()
                time.sleep(min(5.0, args.interval))
                i += 1
                if args.max_iter and i >= args.max_iter:
                    break
                continue

            show_without_focus(hwnd)
            label, countdown = read_keepalive(hwnd)

            if label is None:
                # no keepalive button -> the free session is down
                if should_connect(has_keepalive_button=False,
                                  bootstrap=args.bootstrap):
                    print(f"[openeyes] iter {i}: session down -> reconnecting",
                          flush=True)
                    _shoot(out_dir, hwnd, f"before_reconnect{i}")
                    ok, detail = connect(hwnd, dry_run=not args.go)
                    if ok:
                        stats["reconnects"] += 1
                        # wait for the session to come up instead of
                        # hammering the connect button every round
                        label, countdown = wait_for_keepalive(
                            hwnd, timeout=min(30.0, args.interval))
                    print(f"[openeyes] iter {i}: reconnect {detail}", flush=True)
                elif not args.quiet:
                    print(f"[openeyes] iter {i}: no keepalive button "
                          f"(session down)", flush=True)
                if args.hide:
                    hide_window(hwnd)
                if label is None:
                    next_due = time.monotonic() + args.interval
                else:
                    next_due = time.monotonic() + seconds_until_due(
                        countdown, args.trigger)
            elif not should_renew(countdown, args.trigger):
                if not args.quiet:
                    print(f"[openeyes] iter {i}: timer {countdown}s left, "
                          f"renewal in {seconds_until_due(countdown, args.trigger):.0f}s",
                          flush=True)
                if args.hide:
                    hide_window(hwnd)
                next_due = time.monotonic() + seconds_until_due(countdown, args.trigger)
            else:
                _shoot(out_dir, hwnd, f"before_renew{i}")
                verdict, detail = renew(hwnd, dry_run=not args.go,
                                        retries=args.retries, out_dir=out_dir,
                                        iteration=i)
                _shoot(out_dir, hwnd, f"after_renew{i}")
                if verdict == "hit":
                    stats["renewed"] += 1
                    print(f"[openeyes] iter {i}: RENEWED  {detail}", flush=True)
                elif verdict == "dry-run":
                    print(f"[openeyes] iter {i}: [dry-run] would renew  {detail}",
                          flush=True)
                elif verdict == "gone":
                    stats["missed"] += 1
                    print(f"[openeyes] iter {i}: session lapsed during renewal "
                          f"({detail})", flush=True)
                elif verdict == "unknown":
                    stats["unknown"] += 1
                    print(f"[openeyes] iter {i}: renewed (no countdown to verify) "
                          f"{detail}", flush=True)
                else:
                    stats["missed"] += 1
                    print(f"[openeyes] iter {i}: RENEWAL FAILED  {detail}",
                          flush=True)
                if verdict in ("hit", "dry-run", "unknown") and args.hide:
                    hide_window(hwnd)
                next_due = time.monotonic() + (
                    seconds_until_due(countdown, args.trigger) if verdict != "hit"
                    else max(2.0, seconds_until_due(19, args.trigger)))

            # wait until the next round is due (never touch the UI while waiting)
            while True:
                remaining = next_due - time.monotonic()
                if remaining <= 0:
                    break
                time.sleep(min(0.5, remaining))

            i += 1
            if args.max_iter and i >= args.max_iter:
                break
    except KeyboardInterrupt:
        print("[openeyes] interrupted by user")

    elapsed = time.time() - t_start
    print(f"[openeyes] summary: rounds={stats['rounds']} renewed={stats['renewed']} "
          f"missed={stats['missed']} unknown={stats['unknown']} "
          f"reconnects={stats['reconnects']} relaunches={stats['relaunches']} "
          f"elapsed={elapsed:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
