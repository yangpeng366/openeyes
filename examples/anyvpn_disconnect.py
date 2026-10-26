"""Disconnect the Any VPN session.

Used by launch-anyvpn-keepalive.ps1: double-clicking the shortcut a second
time stops the keepalive loop *and* really disconnects Any VPN, instead of
leaving a dangling tunnel behind.

The connect button doubles as the disconnect button — it is labelled
'Disconnect' while a session is up — so the label is checked before and
after the click.

Exit codes: 0 = disconnected (or nothing to disconnect), 1 = not confirmed.
"""
from __future__ import annotations

import sys
import time

from openeyes import click_element, detect_elements, ensure_dpi_awareness, find_elements

ensure_dpi_awareness()

import win32gui  # noqa: E402

CONNECT_AID = "ConnectButton"
SW_SHOWNOACTIVATE = 4


def find_anyvpn_window() -> int | None:
    hits: list[int] = []

    def cb(h, _):
        if (win32gui.GetWindowText(h) == "Any VPN"
                and "ApplicationFrameWindow" in win32gui.GetClassName(h)):
            hits.append(h)

    win32gui.EnumWindows(cb, None)
    return hits[0] if hits else None


def button_label(hwnd: int) -> str:
    try:
        elems = detect_elements(hwnd)
    except Exception:
        return ""
    hit = find_elements(elems, automation_id=CONNECT_AID)
    return (hit[0].name or "").strip() if hit else ""


def main() -> int:
    hwnd = find_anyvpn_window()
    if hwnd is None:
        print("[openeyes] Any VPN has no window (app closed, or the free "
              "session already ended) - nothing to disconnect.")
        return 0

    # a minimised window has an empty accessibility tree
    try:
        if win32gui.IsIconic(hwnd) or not win32gui.IsWindowVisible(hwnd):
            win32gui.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
            time.sleep(0.8)
    except Exception:
        pass

    label = button_label(hwnd)
    if "disconnect" not in label.lower():
        print(f"[openeyes] no live session to disconnect "
              f"(button says {label!r}).")
        return 0

    report = click_element(hwnd, automation_id=CONNECT_AID, control_type="Button",
                           method="auto", dry_run=False)
    if not report.get("ok"):
        print(f"[openeyes] disconnect click failed: {report.get('error')}",
              file=sys.stderr)
        return 1

    for _ in range(10):
        time.sleep(0.5)
        now = button_label(hwnd)
        if "secure my connection" in now.lower():
            print("[openeyes] Any VPN disconnected.")
            return 0
    print(f"[openeyes] disconnect not confirmed within 5s "
          f"(button says {button_label(hwnd)!r}).", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
