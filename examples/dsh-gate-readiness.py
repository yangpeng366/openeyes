"""Check whether the dsh web gate is ready for Section 4 acceptance.

This probe is intentionally read-only: it does not start dsh, launch a browser,
or send a ``browser_click`` request. It only checks whether the configured URL
returns HTTP 200 and reports the next executable action.
"""

from __future__ import annotations

import argparse
import json
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_GATE_URL = "http://127.0.0.1:3080/"
DEFAULT_NEXT_RECHECK = "2026-09-10T16:30:00+08:00"
READY_NEXT_ACTION = (
    "Run docs/dsh-web-acceptance.md section 4: open the dsh web UI and issue "
    "the two browser_click dry-run calls."
)
NOT_READY_NEXT_ACTION = (
    "Wait for the dsh web host to return HTTP 200, then rerun this probe."
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--url",
        default=DEFAULT_GATE_URL,
        help="dsh web URL to check (default: %(default)s)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=2.0,
        help="HTTP timeout in seconds (default: %(default)s)",
    )
    parser.add_argument(
        "--next-recheck",
        default=DEFAULT_NEXT_RECHECK,
        help="ISO-8601 timestamp to report when the gate is not ready",
    )
    return parser.parse_args()


def _check_gate(url: str, timeout: float) -> tuple[bool, int | None, str | None]:
    try:
        with urlopen(Request(url, method="GET"), timeout=timeout) as response:
            status_code = int(response.status)
    except HTTPError as exc:
        return False, int(exc.code), str(exc)
    except (URLError, TimeoutError, OSError) as exc:
        return False, None, str(exc)
    return status_code == 200, status_code, None


def main() -> int:
    args = _parse_args()
    ready, status_code, error = _check_gate(args.url, args.timeout)
    result = {
        "gate_url": args.url,
        "ready": ready,
        "status_code": status_code,
        "error": error,
        "next_recheck": args.next_recheck,
        "next_action": READY_NEXT_ACTION if ready else NOT_READY_NEXT_ACTION,
    }
    print(json.dumps(result, ensure_ascii=False))
    return 0 if ready else 2


if __name__ == "__main__":
    sys.exit(main())
# 2026-09-03: added readiness probe for dsh web gate (round 107)
