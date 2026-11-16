"""Record a trajectory whose steps carry judgeable expectations.

`Recorder` writes no expectation by default, so `eyes record replay <t> --live`
on a plain recording can only come back `unverified`. A step becomes provable
when the recording carries ``meta={"expect": ...}``: the replayer then checks
the expectation *before* acting (it must not already hold, otherwise the
observation proves nothing) and polls for it after the action.

The demo records the smallest honest case: open a menu, and expect one of the
menu items that was NOT on screen before the click to appear. The revealed
name is learned by opening the menu once and restoring with Esc, so the
expectation is a real observation instead of a guess.

Run:
    python examples/record_demo_expect.py                  # discover + dry-run plan
    python examples/record_demo_expect.py --live           # real input + judging
    python examples/record_demo_expect.py --live --with-failure
    python examples/record_demo_expect.py --window 记事本 --live

Expectation shapes the replayer understands (openeyes/record/replayer.py):
    meta={"expect": "退出"}                                  # element name contains
    meta={"expect": {"element": {"name_contains": "退出"}}}
    meta={"expect": {"name_contains": "退出", "control_type": "MenuItem"}}
    meta={"expect": {"element": {...}, "present": False}}     # expect it to vanish
    meta={"expect": {"window_title_contains": "*"}}           # title must change
    meta={"expect_window_title": "无标题 - 记事本"}             # shorthand
    meta={"verify": ...}                                      # alias of expect

``timeout`` / ``interval`` / ``retries`` inside the payload override the
replayer defaults for that step only.

Anything that cannot be falsified (empty dict, unknown keys, only intent /
input_text / hwnd / window_title-as-context) is reported as `unverified` with
a named reason - never as a silent success.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import openeyes  # noqa: E402 - sys.path is bootstrapped above
from openeyes.core.focus import focus_window_checked  # noqa: E402
from openeyes.record import Recorder, Replayer, Trajectory  # noqa: E402


def _activate(hwnd: int) -> dict:
    """Bring the window to the foreground and report what really happened."""
    return focus_window_checked(int(hwnd), dry_run=False, restore=True)


def _safe(text: str) -> None:
    """Print even when the console codepage cannot encode the text."""
    stream = sys.stdout
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = getattr(stream, "encoding", None) or "utf-8"
        stream.write(text.encode(encoding, "replace").decode(encoding, "replace") + "\n")


def _names(elements) -> set[str]:
    return {(e.name or "").strip() for e in elements if (e.name or "").strip()}


def pick_window(substring: str):
    windows = [w for w in openeyes.list_windows() if (w.title or "").strip()]
    if substring:
        matches = [w for w in windows if substring.lower() in w.title.lower()]
    else:
        matches = [w for w in windows
                   if any(k in w.title for k in ("记事本", "Notepad"))] or windows
    return matches[0] if matches else None


def pick_anchor(elements):
    """Prefer a named menu entry point; fall back to any addressable control.

    A bare ``MenuBar`` has no name, so its click point is whatever happens to
    sit at its center - the same automation_id does not reproduce the same
    menu later. A named ``MenuItem`` is both clickable and reproducible.
    """
    def kind(elem) -> str:
        return (elem.control_type or "").lower()

    def named(elem) -> bool:
        return bool((elem.name or "").strip())

    # A bare ``MenuBar`` has no name, so clicking it means clicking whatever
    # happens to sit at its center - not reproducible on replay. Named
    # controls win; an unnamed one is only used when nothing else is left.
    groups = (
        [e for e in elements if kind(e) == "menuitem" and named(e)],
        [e for e in elements
         if kind(e) in ("menu", "menubar", "splitbutton") and named(e)],
        [e for e in elements if e.interactive and named(e)],
        [e for e in elements if e.interactive
         and (e.automation_id or "").strip()],
    )
    for group in groups:
        if group:
            return group[0]
    return None


def learn_revealed_names(hwnd: int, anchor, attempts: int = 2) -> list[str]:
    """Open the anchor once, note what appeared, restore with Esc.

    A menu can already be open (a previous live run, a user click), in which
    case the first click *closes* it and nothing is revealed. Normalising with
    Esc first, and retrying once, keeps the learned name honest.
    """
    for _ in range(max(1, attempts)):
        openeyes.send_hotkey("esc")
        time.sleep(0.3)
        # The click below is a real mouse click: it only reaches this window
        # when this window is in front.
        _activate(hwnd)
        time.sleep(0.2)
        before = _names(openeyes.detect_elements(hwnd, restore=True))
        openeyes.click_by_selector(
            hwnd=hwnd,
            name_contains=(anchor.name or None),
            control_type=(anchor.control_type or None),
            automation_id=((anchor.automation_id or "").strip() or None),
            dry_run=False,
        )
        time.sleep(0.8)
        after = _names(openeyes.detect_elements(hwnd, restore=True))
        revealed = sorted(after - before)
        openeyes.send_hotkey("esc")
        time.sleep(0.3)
        if revealed:
            return revealed
    return []


def choose_expect(revealed: list[str]) -> str:
    """Pick the most stable revealed name to assert on.

    Quoted or punctuation-heavy UIA names change with wording and locale, so a
    plain alphanumeric label is the better proof-of-effect target.
    """
    simple = [n for n in revealed if all(ch.isalnum() or ch in " _-" for ch in n)]
    pool = simple or revealed
    return pool[0] if pool else ""


def build_trajectory(window, anchor, revealed: list[str], failure: bool) -> Trajectory:
    recorder = Recorder()
    recorder.start(
        name="openeyes-expect-demo",
        app_name=window.class_name or "unknown",
        description=f"Open a menu in {window.title} and expect a revealed item",
    )

    expect_target = choose_expect(revealed)
    if expect_target:
        recorder.record_click(
            hwnd=window.hwnd,
            element=anchor,
            intent=f"Open '{anchor.name}' - a judgeable item '{expect_target}' must appear",
            window_title=window.title,
            pid=window.pid,
            meta={
                "expect": {
                    "element": {"name_contains": expect_target},
                    "timeout": 3.0,
                }
            },
        )
    else:
        # Honest fallback: no revealed name, so no observation can prove the
        # click worked. The step will be reported `unverified`, not faked.
        recorder.record_click(
            hwnd=window.hwnd,
            element=anchor,
            intent=f"Open '{anchor.name}' (nothing was revealed, so no expectation)",
            window_title=window.title,
            pid=window.pid,
        )

    # A hotkey with no expectation: delivered, but nothing judges the effect.
    recorder.record_hotkey("esc", intent="close the menu")

    if failure:
        # Deliberately unsatisfiable: proves `failed` and exit code 1.
        recorder.record_click(
            hwnd=window.hwnd,
            element=anchor,
            intent="Deliberately impossible expectation",
            window_title=window.title,
            pid=window.pid,
            meta={"expect": {"element": {"name_contains": "openeyes-no-such-element"},
                             "timeout": 1.0}},
        )

    return recorder.stop()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--window", default="", help="window title substring (default: Notepad)")
    parser.add_argument("--live", action="store_true", help="send real input and judge each step")
    parser.add_argument("--activate", action="store_true",
                        help="with --live: bring the window to the foreground first "
                             "(real input lands on whatever is in front)")
    parser.add_argument("--with-failure", action="store_true",
                        help="append one unsatisfiable step (failed + exit 1)")
    parser.add_argument("--out", default=str(HERE / "record-out" / "trajectory_expect.jsonl"))
    args = parser.parse_args()

    window = pick_window(args.window)
    if window is None:
        _safe("No window matched. Open Notepad (or pass --window <substring>) and retry.")
        return 2

    # Live input goes to whatever is foreground: a background window receives
    # nothing, and the click lands on the window that happens to be in front.
    # Focus first so a `failed` verdict means the effect did not happen, not
    # that the input went somewhere else.
    focus = _activate(window.hwnd)
    _safe(f"focus  : {focus}")
    time.sleep(0.4)

    elements = openeyes.detect_elements(window.hwnd, restore=True)
    anchor = pick_anchor(elements)
    if anchor is None:
        _safe(f"No addressable control in {window.title!r}.")
        return 2

    revealed = learn_revealed_names(window.hwnd, anchor)
    _safe(f"window : {window.title!r} (hwnd={window.hwnd}, {len(elements)} elements)")
    _safe(f"anchor : {anchor.control_type} {anchor.name!r} aid={anchor.automation_id!r}")
    _safe(f"revealed by the click: {revealed[:6] or '(nothing)'}")
    if not revealed:
        _safe("  ! the click revealed nothing, so this trajectory can only be "
              "`unverified`.")
        _safe(f"  ! live input goes to the FOREGROUND window; activate the target "
              f"first: eyes focus --window {window.hwnd} --go")

    trajectory = build_trajectory(window, anchor, revealed, args.with_failure)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    trajectory.save(str(out))
    _safe(f"\nwrote {out} ({len(trajectory.steps)} steps)")

    report = Replayer(dry_run=not args.live,
                      activate=args.activate and args.live).replay(trajectory)
    mode = "live" if args.live else "dry-run"
    _safe(f"[{mode}] replay of {trajectory.name}")
    for item in report.actions:
        source = item.get("expectation_source", "")
        status = item.get("status") or item.get("plan", {}).get("kind")
        reason = item.get("reason", "")
        _safe(f"{item['index']}: {status} {reason}  (expectation: {source})".rstrip())
    for item in report.skipped:
        _safe(f"{item['index']}: skipped {item.get('reason', '')}".rstrip())

    counts = report.counts()
    _safe(f"{counts['verified']} verified / {counts['unverified']} unverified / "
          f"{counts['failed']} failed")
    _safe("exit code would be 1" if counts["failed"] else "exit code would be 0")

    if args.live and revealed:
        _safe("\nExpected on a healthy run: step 0 verified, step 1 unverified"
              + (", step 2 failed (deliberate)." if args.with_failure else "."))

    plan_path = out.with_suffix(".report.json")
    plan_path.write_text(json.dumps(report.to_dict(), indent=2, ensure_ascii=False),
                         encoding="utf-8")
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
