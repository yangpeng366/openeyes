"""OpenEyes showcase — keep Any VPN's free session alive.

Default behavior is DRY-RUN. Pass --go to actually click.

Usage:
    # dry-run (no clicks): just print what would be clicked
    python examples\anyvpn_keepalive.py --interval 18

    # real click loop: only click Click-to-reset-style buttons
    python examples\anyvpn_keepalive.py --interval 18 --go

    # real click loop, including one-time Secure my connection bootstrap
    python examples\anyvpn_keepalive.py --interval 18 --go --bootstrap

    # bounded test: 5 iterations only
    python examples\anyvpn_keepalive.py --interval 18 --max-iter 5

    # capture before/after screenshots per click for evidence
    python examples\anyvpn_keepalive.py --interval 18 --go --bootstrap --screenshot-dir shots/anyvpn

Notes:
    - --bootstrap is opt-in: by default we never click "Secure my connection"
      (that button changes network routing — kept explicit per the original
      NVK boundary).
    - The window is focused before each click (UWP needs this; otherwise the
      click may silently fail to the background window).
    - Each click is followed by a re-detect: if the button label changes
      (e.g. "Click to reset" -> disappears after a successful reset) we
      count it as a confirmed hit. If the label is unchanged we count it as
      an unverified hit and warn once per session.
"""
from __future__ import annotations
import argparse
import sys
import time
from pathlib import Path

from openeyes import find_window, detect_elements, find_elements, click_xy
from openeyes.actuators.win32 import focus_window

try:
    import win32api
    import win32con
    import win32gui
except Exception:  # pragma: no cover - fall back to focus-only behaviour
    win32api = win32con = win32gui = None

# AnyVPN's own top-right "hide" (collapse-to-bubble) button uses the Segoe
# MDL2 "Hide" glyph; the OS title-bar buttons sit just above it.
HIDE_GLYPH = "\ue115"

# Targets that just keep an existing VPN session alive. SAFE to click.
KEEPALIVE_TARGETS = [
    "Click to reset",
    "Reset",
    "Reconnect",
]

# Targets that change network state. Only used with --bootstrap.
BOOTSTRAP_TARGETS = [
    "Secure my connection",
]


def _resolve_target(hwnd: int, *, allow_bootstrap: bool):
    """Re-detect and return (button_name, element). Returns (None, None) on miss."""
    elems = detect_elements(hwnd, restore=True)
    if allow_bootstrap:
        for kw in BOOTSTRAP_TARGETS + KEEPALIVE_TARGETS:
            matches = find_elements(elems, name_contains=kw, control_type="Button")
            if matches:
                return kw, matches[0]
        return None, None
    for kw in KEEPALIVE_TARGETS:
        matches = find_elements(elems, name_contains=kw, control_type="Button")
        if matches:
            return kw, matches[0]
    return None, None


def _window_pos(hwnd: int) -> tuple[int, int, int, int]:
    """Return (x, y, w, h) of the window via fresh EnumWindows."""
    wins = find_window(title_contains="Any VPN",
                       class_name="ApplicationFrameWindow")
    if not wins:
        return (0, 0, 0, 0)
    w = wins[0]
    return (w.x, w.y, w.w, w.h)


def _snapshot_foreground() -> tuple[int, tuple[int, int] | None]:
    """Return (foreground_hwnd, cursor_pos) before this round touches the UI."""
    if win32gui is None:
        return 0, None
    try:
        return win32gui.GetForegroundWindow(), win32api.GetCursorPos()
    except Exception:
        return 0, None


def _is_our_console(hwnd: int) -> bool:
    if win32gui is None:
        return False
    try:
        return hwnd == win32gui.GetConsoleWindow()
    except Exception:
        return False


def _restore_foreground(fg_hwnd: int, cursor_pos, screen_hwnd: int) -> None:
    """Give the cursor + window focus back to what the user had before."""
    if cursor_pos is not None and win32api is not None:
        try:
            win32api.SetCursorPos(cursor_pos)
        except Exception:
            pass
    if not fg_hwnd or _is_our_console(fg_hwnd) or fg_hwnd == screen_hwnd:
        return
    if win32gui is None:
        return
    try:
        if win32gui.GetForegroundWindow() == fg_hwnd:
            return
        win32gui.ShowWindow(fg_hwnd, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(fg_hwnd)
    except Exception:
        try:
            from pywinauto import Desktop
            Desktop(backend="uia").window(handle=fg_hwnd).set_focus()
        except Exception:
            pass


def _ensure_visible(hwnd: int) -> None:
    """Restore a minimized window so the next click can actually land."""
    if win32gui is None:
        return
    try:
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            time.sleep(0.25)
    except Exception:
        pass

def _find_hide_button(hwnd: int, rect: tuple[int, int, int, int]):
    """Return the control used to hide the window after a successful reset.

    The user confirmed the hide affordance is the title-bar '-' (minimize)
    button — visually a single horizontal dash in the top-right corner. The
    in-app Segoe MDL2 'Hide' glyph (\ue115) did NOT actually hide the window
    in testing, so we never prefer it; it is kept only as a last resort.

    Clicking '-' minimizes AnyVPN; ``_ensure_visible`` at the top of the next
    round restores it so screen-coordinate clicks can still land.

    ``rect`` is (left, top, right, bottom) of the window.
    """
    # The caller's rect may be stale (e.g. the window was minimized and the
    # rect was -32000). detect_elements(restore=True) below restores it, so
    # re-read the real rect now for the bounds sanity-check.
    if win32gui is not None:
        try:
            l, t, r, b = win32gui.GetWindowRect(hwnd)
            if r - l > 100 and b - t > 100:
                rect = (l, t, r, b)
        except Exception:
            pass
    try:
        elems = detect_elements(hwnd, restore=True)
    except Exception:
        return None

    def _in_rect(e):
        x = getattr(e.center, "x", 0)
        y = getattr(e.center, "y", 0)
        return rect[0] <= x <= rect[2] and rect[1] <= y <= rect[3]

    # 1) Title-bar minimize button ('-'). Localized names: 最小化 / Minimize.
    for e in elems:
        if getattr(e, "control_type", "") == "Button" and _in_rect(e):
            name = (e.name or "")
            if "最小化" in name or "minimize" in name.lower():
                return e
    # 2) Unnamed Button pinned to the title-bar strip (very top of window):
    #    the '-' has no localized name on some locales; pick the top-most
    #    unnamed Button in the right-hand cluster of the title bar.
    title_bar_btns = []
    for e in elems:
        if getattr(e, "control_type", "") == "Button" and _in_rect(e):
            cy = getattr(e.center, "y", 0) - rect[1]
            if cy <= 60:  # title-bar strip (~top 60px)
                title_bar_btns.append(e)
    if title_bar_btns:
        # rightmost of the top cluster is 'close'; minimize is left of it.
        title_bar_btns.sort(key=lambda e: getattr(e.center, "x", 0))
        if len(title_bar_btns) >= 2:
            return title_bar_btns[0]  # leftmost = minimize ('-')
    # 3) Last resort: the in-app Hide glyph (kept for backward compat).
    for e in elems:
        if getattr(e, "name", "") == HIDE_GLYPH and _in_rect(e):
            return e
    return None


def _shoot(out_dir: Path, hwnd: int, label: str) -> None:
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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--go", action="store_true",
                    help="actually click. Default is dry-run.")
    ap.add_argument("--interval", type=float, default=18.0,
                    help="seconds between iterations (default 18)")
    ap.add_argument("--max-iter", type=int, default=0, help="0 = run forever")
    ap.add_argument("--title-contains", default="Any VPN")
    ap.add_argument("--bootstrap", action="store_true",
                    help="allow one-time click on 'Secure my connection' to "
                         "start the VPN. By default we only click keepalive "
                         "buttons (safer — does not change network routing).")
    ap.add_argument("--screenshot-dir", default=None,
                    help="if set, save before/after screenshots per click")
    ap.add_argument("--quiet", action="store_true",
                    help="only print hits / errors, not misses")
    args = ap.parse_args()

    out_dir = Path(args.screenshot_dir) if args.screenshot_dir else None

    wins = find_window(title_contains=args.title_contains,
                       class_name="ApplicationFrameWindow")
    if not wins:
        print(f"[openeyes] window {args.title_contains!r} not found", file=sys.stderr)
        return 2
    hwnd = wins[0].hwnd
    pos0 = (wins[0].x, wins[0].y, wins[0].w, wins[0].h)
    print(f"[openeyes] using hwnd={hwnd} dry_run={not args.go} "
          f"interval={args.interval}s bootstrap={args.bootstrap}")
    print(f"[openeyes] window pos: x={pos0[0]} y={pos0[1]} "
          f"size={pos0[2]}x{pos0[3]}")

    stats = {"hits": 0, "misses": 0, "unverified": 0, "window_moves": 0,
             "bootstrapped": False}
    t_start = time.time()
    last_pos = pos0
    last_warn_unverified = False

    i = 0
    snapshot = (0, None)
    try:
        while True:
            # 1) Re-find window (hwnd could change if app restarted)
            wins = find_window(title_contains=args.title_contains,
                               class_name="ApplicationFrameWindow")
            if not wins:
                print(f"[openeyes] iter {i}: window disappeared — aborting")
                return 3
            hwnd = wins[0].hwnd
            pos = (wins[0].x, wins[0].y, wins[0].w, wins[0].h)
            moved = abs(pos[0] - last_pos[0]) > 50 or abs(pos[1] - last_pos[1]) > 50
            if moved:
                stats["window_moves"] += 1
                if not args.quiet:
                    print(f"[openeyes] iter {i}: window moved "
                          f"{last_pos[:2]} -> {pos[:2]}  (using new center)")
                last_pos = pos

            # Remember the user's active window + cursor before this round
            # touches the UI, so we can give them both back afterwards.
            if args.go:
                snapshot = _snapshot_foreground()

            # 2) Resolve target
            kw, target = _resolve_target(hwnd, allow_bootstrap=args.bootstrap)
            if target is None:
                stats["misses"] += 1
                if not args.quiet:
                    print(f"[openeyes] iter {i}: no matching button")
            else:
                line = (f"[openeyes] iter {i}: kw={kw!r} button={target.name!r} "
                        f"@ ({target.center.x},{target.center.y})  "
                        f"size={target.bbox.w}x{target.bbox.h}")
                if args.go:
                    if kw == "Secure my connection" and not stats["bootstrapped"]:
                        # one-time notice when bootstrap is consumed
                        print(f"[openeyes] BOOTSTRAP: clicking Secure my connection "
                              f"(one-time, changes network routing)")
                        stats["bootstrapped"] = True
                    _shoot(out_dir, hwnd, f"before_iter{i}")
                    _ensure_visible(hwnd)
                    focus_window(hwnd)
                    time.sleep(0.15)
                    click_xy(target.center.x, target.center.y)
                    time.sleep(0.4)
                    _shoot(out_dir, hwnd, f"after_iter{i}")
                    print(line + "  CLICKED")

                    # 3) Verify
                    kw_after, target_after = _resolve_target(
                        hwnd, allow_bootstrap=False)
                    reset_ok = False
                    if kw_after is None or target_after is None:
                        # no keepalive button anymore -> reset likely succeeded
                        stats["hits"] += 1
                        reset_ok = True
                    elif target_after.name != target.name:
                        # button changed (e.g. Click to reset -> other state)
                        stats["hits"] += 1
                        reset_ok = True
                        print(f"[openeyes] iter {i}: verified — button changed "
                              f"to {target_after.name!r}")
                    else:
                        stats["unverified"] += 1
                        if not last_warn_unverified:
                            print(f"[openeyes] iter {i}: WARNING — button label "
                                  f"unchanged after click ({target.name!r}). "
                                  f"Will only warn once.")
                            last_warn_unverified = True

                    # 4) Hide the window after a confirmed reset
                    if reset_ok:
                        rect = (pos[0], pos[1], pos[0] + pos[2], pos[1] + pos[3])
                        hide_btn = _find_hide_button(hwnd, rect)
                        if hide_btn is not None:
                            focus_window(hwnd)
                            time.sleep(0.1)
                            click_xy(hide_btn.center.x, hide_btn.center.y)
                            time.sleep(0.4)
                            hidden = bool(win32gui and win32gui.IsIconic(hwnd)) \
                                if win32gui else None
                            if not hidden and win32gui is not None:
                                # First click on an inactive window's title-bar
                                # button only activates it; fall back to a
                                # direct SW_MINIMIZE for reliability.
                                try:
                                    win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
                                    time.sleep(0.2)
                                    hidden = bool(win32gui.IsIconic(hwnd))
                                    via_api = True
                                except Exception:
                                    via_api = False
                            else:
                                via_api = False
                            print(f"[openeyes] iter {i}: clicked hide @ "
                                  f"({hide_btn.center.x},{hide_btn.center.y}) "
                                  f"name={hide_btn.name!r} minimized={hidden} "
                                  f"via_api={via_api}")
                        elif not args.quiet:
                            print(f"[openeyes] iter {i}: hide button not found")
                else:
                    print(line + "  [dry-run]")

            # Give the mouse + focus back to the pre-round window
            if args.go:
                _restore_foreground(snapshot[0], snapshot[1], hwnd)

            i += 1
            if args.max_iter and i >= args.max_iter:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("[openeyes] interrupted by user")

    elapsed = time.time() - t_start
    print(f"[openeyes] summary: iters={i} hits={stats['hits']} "
          f"misses={stats['misses']} unverified={stats['unverified']} "
          f"window_moves={stats['window_moves']} bootstrapped={stats['bootstrapped']} "
          f"elapsed={elapsed:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())