"""Measure a second desktop, and leave this one exactly as it was.

Three questions, and the numbers that answer them:

1. what displays this machine has, what DPI each runs at, and whether
   any *virtual* display driver is actually driving one of them;
2. whether a cursor overlay can be created and painted on a desktop made
   with ``CreateDesktop``, without moving this desktop's foreground
   window or pointer by a single pixel;
3. whether anything can then read that window back - ``PrintWindow``
   can, a screen grab cannot, because a desktop nobody is showing has
   no screen.

    python examples\\second-desktop-probe.py
    python examples\\second-desktop-probe.py --label B --json

The desktop is created, measured and closed. No click is sent, no key
is typed, the pointer is not moved, no display driver is installed and
``SwitchDesktop`` is never called - the desktop in front of you is the
one you keep looking at.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from openeyes.agent.virtual_desktop import (  # noqa: E402
    FAIL,
    display_topology,
    probe_second_desktop,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--agent-id", default="second-desktop")
    parser.add_argument("--label", default="B")
    parser.add_argument("--color", default="#00c853")
    parser.add_argument("--x", type=int, default=300)
    parser.add_argument("--y", type=int, default=300)
    parser.add_argument("--hold", type=float, default=1.5)
    parser.add_argument("--json", action="store_true",
                        help="print the whole report as JSON")
    args = parser.parse_args()

    if args.json:
        print("topology:")
        print(json.dumps(display_topology().to_dict(), indent=2))
        report = probe_second_desktop((args.x, args.y), agent_id=args.agent_id,
                                      label=args.label, color=args.color,
                                      hold=args.hold)
        print("second desktop:")
        print(json.dumps(report.to_dict(), indent=2))
        return 0 if report.overall != FAIL else 1

    report = probe_second_desktop((args.x, args.y), agent_id=args.agent_id,
                                  label=args.label, color=args.color,
                                  hold=args.hold)
    for line in report.lines():
        print(line)
    return 0 if report.overall != FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
