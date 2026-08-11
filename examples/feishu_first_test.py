"""OpenEyes first showcase — drive the Feishu (Lark) client.

Default behavior is DRY-RUN: enumerates the window, prints matches, does
NOT click. Pass --go to actually click.

Usage:
    python examples\feishu_first_test.py --dry-run
    python examples\feishu_first_test.py --go
"""
from __future__ import annotations
import argparse
import sys

from openeyes import find_window, capture_window, detect_elements, find_elements
from openeyes import click_by_selector


DEFAULT_TARGETS = [
    "消息",
    "通讯录",
    "日历",
    "文档",
    "云盘",
    "妙记",
]


def pick_hwnd(title_contains: str = "飞书") -> int | None:
    wins = find_window(title_contains=title_contains)
    if not wins:
        # fallback to Lark / 飞书
        wins = find_window(regex=r"飞书|Lark|Feishu")
    if not wins:
        return None
    return wins[0].hwnd


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--go", action="store_true",
                    help="actually click. Default is dry-run.")
    ap.add_argument("--title-contains", default="飞书")
    ap.add_argument("--target", default="消息",
                    help="element name to find and click")
    ap.add_argument("--out-dir", default="shots")
    args = ap.parse_args()

    hwnd = pick_hwnd(args.title_contains)
    if hwnd is None:
        print(f"[openeyes] no window matches {args.title_contains!r}", file=sys.stderr)
        return 2

    print(f"[openeyes] using hwnd={hwnd} dry_run={not args.go}")
    print(f"[openeyes] capturing screenshot...")
    img = capture_window(hwnd)
    import os
    os.makedirs(args.out_dir, exist_ok=True)
    before = f"{args.out_dir}/feishu_before.png"
    img.save(before, "PNG")
    print(f"[openeyes] saved {before}  size={img.size}")

    elems = detect_elements(hwnd, restore=True)
    print(f"[openeyes] enumerated {len(elems)} elements")

    target_name = args.target
    matches = find_elements(elems, name_contains=target_name)
    if not matches:
        print(f"[openeyes] no element matches {target_name!r}", file=sys.stderr)
        print("[openeyes] known candidates:", DEFAULT_TARGETS)
        return 3

    target = matches[0]
    print(f"[openeyes] match: {target.control_type} {target.name!r} "
          f"@ ({target.center.x},{target.center.y})  size={target.bbox.w}x{target.bbox.h}")

    if args.go:
        clicked = click_by_selector(hwnd, name_contains=target_name, dry_run=False)
        print(f"[openeyes] clicked {clicked.name!r}")
        import time; time.sleep(0.5)
        img2 = capture_window(hwnd)
        after = f"{args.out_dir}/feishu_after.png"
        img2.save(after, "PNG")
        print(f"[openeyes] saved {after}")
    else:
        print(f"[openeyes] DRY-RUN: would click {target.name!r} "
              f"@ ({target.center.x},{target.center.y})")
        print("[openeyes] pass --go to actually click")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())