"""Measure coordinates on this machine's real monitors, scaling included.

Dry-run by default: it enumerates every monitor and its DPI, plans the
three scenarios below, and prints where each click *would* go. Nothing is
clicked and no window is created. ``--go`` opens a real window on the
display under test, sends a real left click, and checks that the window
received it at the point that was computed.

    python examples\\multi-monitor-smoke.py
    python examples\\multi-monitor-smoke.py --go
    python examples\\multi-monitor-smoke.py --go --json

Scenarios:

1. a window that is not at (0, 0) - the one-window-origin shift;
2. a display at 125 % or 130 % scaling - CSS pixels vs device pixels;
3. a secondary monitor at a negative coordinate - left of the primary.

A scenario this machine cannot exercise is reported as ``SKIP`` with the
numbers it was decided on (which displays, which scales). There is no
path that turns "not testable here" into ``PASS``.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from openeyes.agent.display_smoke import (  # noqa: E402
    FAIL,
    run,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--go", action="store_true",
                        help="really create a window and click; default is "
                             "dry-run and moves nothing")
    parser.add_argument("--json", action="store_true",
                        help="print the whole report as JSON")
    parser.add_argument("--tolerance", type=int, default=2,
                        help="how many pixels the received click may differ "
                             "from the sent point (default 2)")
    args = parser.parse_args()

    report = run(go=args.go, tolerance=args.tolerance)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    else:
        for line in report.lines():
            print(line)
    return 0 if report.overall != FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
