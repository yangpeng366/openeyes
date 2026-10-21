"""Check whether the dsh web gate is ready for Section 4 acceptance.

This probe is intentionally read-only: it does not start dsh, launch a browser,
or send a ``browser_click`` request. It only checks whether the configured URL
returns a response that proves the host is up, and reports the next executable
action.

Why HTTP 401 counts as ready
----------------------------
As of dsh 0.1.2-rc.1 the web host serves ``http://127.0.0.1:3080/`` behind a
one-shot access token printed to stdout at startup
(``http://127.0.0.1:3080/?token=...``). Hitting the bare URL without the token
returns **401 Unauthorized** — that is the host saying "I am up; show me the
token", not "I am down". Treating 401 as not-ready made the scheduled patrol
wait for months on a healthy host.
"""

from __future__ import annotations

import argparse
import json
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_GATE_URL = "http://127.0.0.1:3080/"
DEFAULT_NEXT_RECHECK = "2026-09-10T16:30:00+08:00"
#: Status codes that prove the host process is listening and answering.
ALIVE_STATUS_CODES = frozenset({200, 401})
READY_NEXT_ACTION = (
    "Run docs/dsh-web-acceptance.md section 4: open the dsh web UI and issue "
    "the two browser_click dry-run calls."
)
TOKEN_NEXT_ACTION = (
    "Host is up (HTTP 401 = token required). Re-run `dsh --profile web` "
    "(or read its stdout) for the `?token=` URL, then open that URL and "
    "continue docs/dsh-web-acceptance.md section 4."
)
NOT_READY_NEXT_ACTION = (
    "Wait for the dsh web host to return HTTP 200 or 401, then rerun this probe."
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
        status_code = int(exc.code)
        if status_code in ALIVE_STATUS_CODES:
            return True, status_code, None
        return False, status_code, str(exc)
    except (URLError, TimeoutError, OSError) as exc:
        return False, None, str(exc)
    return status_code in ALIVE_STATUS_CODES, status_code, None


def _next_action(ready: bool, status_code: int | None) -> str:
    if not ready:
        return NOT_READY_NEXT_ACTION
    if status_code == 401:
        return TOKEN_NEXT_ACTION
    return READY_NEXT_ACTION


def main() -> int:
    args = _parse_args()
    ready, status_code, error = _check_gate(args.url, args.timeout)
    result = {
        "gate_url": args.url,
        "ready": ready,
        "status_code": status_code,
        "error": error,
        "next_recheck": args.next_recheck,
        "next_action": _next_action(ready, status_code),
    }
    print(json.dumps(result, ensure_ascii=False))
    return 0 if ready else 2


if __name__ == "__main__":
    sys.exit(main())