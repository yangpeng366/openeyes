"""OpenEyes showcase — keep Any VPN's free session alive.

Default behavior is DRY-RUN. Pass --go to actually click.

Carried over from the NVK MVP. The button labels change between versions;
DEFAULT_TARGETS lists all candidates the script tries in order.

Usage:
    python examples\anyvpn_keepalive.py --dry-run
    python examples\anyvpn_keepalive.py --go --interval 20
"""
from __future__ import annotations
import argparse
import sys
import time

from openeyes import find_window, detect_elements, find_elements, click_xy


DEFAULT_TARGETS = [
    "Secure my connection",
    "Click to reset",
    "Reset",
    "Reconnect",
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--go", action="store_true",
                    help="actually click. Default is dry-run.")
    ap.add_argument("--interval", type=float, default=20.0)
    ap.add_argument("--max-iter", type=int, default=0, help="0 = run forever")
    ap.add_argument("--title-contains", default="Any VPN")
    args = ap.parse_args()

    wins = find_window(title_contains=args.title_contains,
                       class_name="ApplicationFrameWindow")
    if not wins:
        print(f"[openeyes] window {args.title_contains!r} not found", file=sys.stderr)
        return 2
    hwnd = wins[0].hwnd
    print(f"[openeyes] using hwnd={hwnd} dry_run={not args.go}")

    i = 0
    while True:
        elems = detect_elements(hwnd, restore=True)
        target = None
        for kw in DEFAULT_TARGETS:
            matches = find_elements(elems, name_contains=kw, control_type="Button")
            if matches:
                target = matches[0]
                break

        if target is None:
            print(f"[openeyes] iter {i}: no matching button")
        else:
            print(f"[openeyes] iter {i}: {target.name!r} "
                  f"@ ({target.center.x},{target.center.y})  "
                  f"size={target.bbox.w}x{target.bbox.h}")
            if args.go:
                click_xy(target.center.x, target.center.y)
                print(f"[openeyes] clicked")

        i += 1
        if args.max_iter and i >= args.max_iter:
            break
        time.sleep(args.interval)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())