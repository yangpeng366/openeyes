"""Drive a Firefox that lives on another machine (Ubuntu), over SSH.

The browser on the remote box is started with Firefox's Remote Agent
(``--remote-debugging-port``). This script opens an SSH local forward to
that port, opens a WebDriver BiDi session through it, and then lists tabs /
scans the DOM / clicks / types - the same verbs as ``eyes browser ...``,
with the tunnel handled for you.

Why the Host header matters: Firefox checks the Host header of the
incoming request against the address the Remote Agent was told to listen
on. Behind a forward the local port differs from the remote one, so the
header has to name the remote address (``localhost:9222``). Without it the
browser answers 403 and the session never opens.

Everything that changes state is a dry-run until ``--go`` is passed.

Usage:
    python examples/remote_firefox_bidi.py --ssh-host yp-x240 --scan
    python examples/remote_firefox_bidi.py --ssh-host yp-x240 --click "Resend code" --go
    python examples/remote_firefox_bidi.py --ssh-host yp-x240 --type 123456 --go
"""
from __future__ import annotations

import argparse
import json
import sys

from openeyes.backends import bidi
from openeyes.core.tunnel import SshTunnel, TunnelError


def find_by_name(elements, name_contains: str):
    """First element whose name contains the given text, case-insensitive."""
    needle = name_contains.lower()
    for element in elements:
        name = (getattr(element, "name", "") or "").lower()
        if needle in name:
            return element
    return None


def describe(element) -> dict:
    center = getattr(element, "center", None)
    state = getattr(element, "state", None)
    return {
        "control_type": getattr(element, "control_type", ""),
        "name": getattr(element, "name", ""),
        "class_name": getattr(element, "class_name", ""),
        "center": [center.x, center.y] if center else None,
        "enabled": getattr(state, "enabled", None),
        "visible": getattr(state, "visible", None),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ssh-host", required=True,
                    help="ssh destination: an alias from ~/.ssh/config or user@host")
    ap.add_argument("--ssh-config", default=None, help="path to an ssh config file")
    ap.add_argument("--identity-file", default=None, help="private key for the hop")
    ap.add_argument("--local-port", type=int, default=19222,
                    help="local port of the forward (default 19222)")
    ap.add_argument("--remote-port", type=int, default=9222,
                    help="remote Firefox Remote Agent port (default 9222)")
    ap.add_argument("--host-header", default=None,
                    help="Host header the remote browser expects (default localhost:<remote-port>)")
    ap.add_argument("--url-contains", default=None,
                    help="only drive the page whose URL contains this")
    ap.add_argument("--scan", action="store_true", help="enumerate interactive elements")
    ap.add_argument("--click", default=None, help="click the first element matching this name")
    ap.add_argument("--type", default=None, help="type text into the first element matching --into")
    ap.add_argument("--into", default=None,
                    help="element name for --type (defaults to the focused element)")
    ap.add_argument("--go", action="store_true",
                    help="actually click/type. Default is dry-run.")
    ap.add_argument("--keep-tunnel", action="store_true",
                    help="leave the forward open after the run")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    host_header = args.host_header or f"localhost:{args.remote_port}"
    report: dict = {"ssh_host": args.ssh_host, "local_port": args.local_port,
                    "host_header": host_header, "ok": False}

    tunnel = SshTunnel(
        ssh_host=args.ssh_host,
        local_port=args.local_port,
        remote_port=args.remote_port,
        identity_file=args.identity_file,
        ssh_config=args.ssh_config,
    )
    try:
        tunnel.start()
    except TunnelError as exc:
        print(f"[openeyes] tunnel failed: {exc}", file=sys.stderr)
        report["error"] = str(exc)
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        return 4

    conn = None
    try:
        conn = bidi.connect(port=args.local_port, host_header=host_header,
                            url_contains=args.url_contains)
        tabs = conn.tabs()
        report["tabs"] = tabs
        if not args.json:
            print(f"[openeyes] tunnel {tunnel.forward_spec} -> {args.ssh_host}")
            for tab in tabs:
                print(f"[openeyes] tab {tab['id'][:8]}  {tab['title']!r}  {tab['url']}")

        elements = []
        if args.scan or args.click or args.type:
            elements = bidi.scan_dom(conn)
            report["elements"] = [describe(item) for item in elements]

        if args.scan and not args.json:
            print(f"[openeyes] {len(elements)} interactive elements")
            for item in elements:
                center = getattr(item, "center", None)
                where = f"({center.x},{center.y})" if center else "(?)"
                print(f"[openeyes]   {item.control_type:<10} {item.name!r} {where}")

        if args.click:
            target = find_by_name(elements, args.click)
            if target is None:
                print(f"[openeyes] no element matches {args.click!r}", file=sys.stderr)
                return 3
            report["clicked"] = describe(target)
            if args.go:
                bidi.click_center(conn, target)
                print(f"[openeyes] clicked {target.name!r}")
            else:
                print(f"[openeyes] DRY-RUN: would click {target.name!r}")
                print("[openeyes] pass --go to actually click")

        if args.type:
            needle = args.into or ""
            target = find_by_name(elements, needle) if needle else None
            if needle and target is None:
                print(f"[openeyes] no element matches {needle!r}", file=sys.stderr)
                return 3
            if args.go:
                if target is not None:
                    bidi.focus_element(conn, target)
                bidi.type_text(conn, args.type, element=target)
                print(f"[openeyes] typed {len(args.type)} chars")
            else:
                print(f"[openeyes] DRY-RUN: would type {args.type!r}")
                print("[openeyes] pass --go to actually type")

        report["ok"] = True
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except bidi.BidiError as exc:
        print(f"[openeyes] browser session failed: {exc}", file=sys.stderr)
        report["error"] = str(exc)
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        return 4
    finally:
        if conn is not None:
            conn.close()
        if not args.keep_tunnel:
            tunnel.stop()


if __name__ == "__main__":
    raise SystemExit(main())
