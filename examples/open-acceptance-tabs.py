"""Open the two disposable acceptance pages as new tabs in a debug Edge.

The dsh-web acceptance runbook (docs/dsh-web-acceptance.md section 4) requires
two disposable page targets: one whose URL contains ``target-a`` and a decoy
whose URL does not. This launcher opens ``examples/acceptance-pages/target-a.html``
and ``examples/acceptance-pages/decoy.html`` as new tabs in an already-running
Chromium debug browser (default port 9222) via the CDP `/json/new` HTTP
endpoint, which remains compatible when Edge disables the browser-level
WebSocket.

Default behaviour is **dry-run**: it prints the file URLs it would open and
reports whether the debug port is listening, but creates no tabs. Pass ``--go``
to actually open the tabs. Pass ``--close`` to close any tab whose URL contains
``acceptance-pages`` (cleanup).

Exit codes: 0 success, 2 debug port not listening, 3 CDP error.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

CDP_DEFAULT_PORT = 9222
REPO_ROOT = Path(__file__).resolve().parent.parent
PAGES_DIR = REPO_ROOT / "examples" / "acceptance-pages"
TARGET_A = PAGES_DIR / "target-a.html"
DECOY = PAGES_DIR / "decoy.html"
ACCEPTANCE_MARKER = "acceptance-pages"


def _file_url(path: Path) -> str:
    return "file:///" + str(path).replace("\\", "/")


def _port_listening(port: int, timeout: float = 2.0) -> bool:
    import socket
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def _list_tabs(port: int) -> list[dict]:
    url = f"http://127.0.0.1:{port}/json"
    req = urllib.request.Request(url, headers={"Host": "127.0.0.1"})
    with urllib.request.urlopen(req, timeout=5) as r:
        raw = r.read().decode("utf-8", errors="replace")
    return [t for t in json.loads(raw) if t.get("type") == "page"]


def _request_text(url: str, method: str = "GET", timeout: float = 10.0) -> str:
    req = urllib.request.Request(url, method=method, headers={"Host": "127.0.0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def _request_json(url: str, method: str = "GET", timeout: float = 10.0) -> dict:
    raw = _request_text(url, method=method, timeout=timeout)
    return json.loads(raw) if raw.strip() else {}


def open_tabs(port: int) -> list[dict]:
    opened: list[dict] = []
    for page in (TARGET_A, DECOY):
        encoded_url = urllib.parse.quote(_file_url(page), safe="")
        result = _request_json(
            f"http://127.0.0.1:{port}/json/new?{encoded_url}", method="PUT"
        )
        target_id = result.get("id", "")
        opened.append({"file": page.name, "url": _file_url(page),
                        "targetId": target_id})
        time.sleep(0.3)
    return opened


def close_acceptance_tabs(port: int) -> list[str]:
    tabs = _list_tabs(port)
    closed: list[str] = []
    for t in tabs:
        if ACCEPTANCE_MARKER in (t.get("url") or ""):
            _request_text(f"http://127.0.0.1:{port}/json/close/{t['id']}")
            closed.append(t["id"])
    return closed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=CDP_DEFAULT_PORT)
    parser.add_argument("--go", action="store_true",
                        help="actually open the tabs (default: dry-run)")
    parser.add_argument("--close", action="store_true",
                        help="close tabs whose URL contains "
                             "'acceptance-pages'")
    args = parser.parse_args()

    for page in (TARGET_A, DECOY):
        if not page.is_file():
            print(f"missing fixture: {page}", file=sys.stderr)
            return 3

    urls = [_file_url(TARGET_A), _file_url(DECOY)]
    print("acceptance fixtures:")
    for u in urls:
        marker = "  target-a  " if "target-a" in u else "  decoy    "
        print(f"{marker} {u}")

    if not _port_listening(args.port):
        print(f"debug port {args.port} not listening - "
              f"start Edge with --remote-debugging-port={args.port} first",
              file=sys.stderr)
        if args.go:
            return 2
        print("(dry-run: no tabs opened)")
        return 0

    if args.close:
        closed = close_acceptance_tabs(args.port)
        print(f"closed {len(closed)} acceptance tab(s): {closed}")
        return 0

    if not args.go:
        existing = [t for t in _list_tabs(args.port)
                    if ACCEPTANCE_MARKER in (t.get("url") or "")]
        print(f"debug port {args.port} is listening; "
              f"{len(existing)} acceptance tab(s) already open")
        print("(dry-run: pass --go to open the tabs)")
        return 0

    opened = open_tabs(args.port)
    for o in opened:
        print(f"opened {o['file']} -> targetId={o['targetId']}")
    print(f"opened {len(opened)} tab(s); run the dsh-web acceptance now")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
