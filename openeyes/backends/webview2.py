"""Reach the DOM inside a WebView2 desktop app.

Tauri, Wails, Electron and other WebView2 shells paint their UI in a
web view. To UIA such an app looks like a handful of empty panes: the
accessibility tree stops at ``BrowserRootView`` and the DOM is invisible.
The only structured view of what the user actually sees is the DOM, and
the DOM is reachable over the DevTools protocol - if the shell is
started with a debugging port.

WebView2 reads ``WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS`` when it creates
its browser process, so a shell started with that variable in its
environment gets ``--remote-debugging-port`` for free. The shell itself
must be restarted for the flag to take effect; the port stays open for
the lifetime of the web view.

This module is the honest path for such apps: it says clearly when the
port is missing instead of silently falling back to pixels.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

#: The environment variable WebView2 reads when spawning its browser
#: process. Setting it for a shell makes the web view listen on a
#: DevTools port.
WEBVIEW2_ARGS_ENV = "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"

#: Default port for a web view's DevTools endpoint. 9333 is the
#: conventional choice and does not collide with the 9222 used by the
#: OpenEyes-managed Edge.
DEFAULT_WEBVIEW_PORT = 9333


class WebView2Error(RuntimeError):
    """Raised when a web view's DevTools port cannot be reached."""


def debug_args_for(port: int = DEFAULT_WEBVIEW_PORT) -> str:
    """The browser arguments a web view needs to expose a DevTools port."""
    return (
        f"--remote-debugging-port={port} "
        "--remote-allow-origins=*"
    )


def env_for_child(port: int = DEFAULT_WEBVIEW_PORT) -> dict:
    """Environment additions that give a child web view its DevTools port."""
    env = dict(os.environ)
    existing = env.get(WEBVIEW2_ARGS_ENV, "").strip()
    args = debug_args_for(port)
    env[WEBVIEW2_ARGS_ENV] = f"{existing} {args}".strip() if existing else args
    return env


def launch_shell(exe: str, *, args: tuple[str, ...] = (),
                 port: int = DEFAULT_WEBVIEW_PORT,
                 wait_s: float = 20.0) -> dict:
    """Start a desktop shell with a DevTools port enabled for its web view.

    The shell must be closed first - a running shell keeps its browser
    process, and the environment variable is only read at creation time.
    """
    report: dict = {"launched": False, "pid": 0, "port": port,
                    "method": "webview2-env", "detail": ""}
    try:
        proc = subprocess.Popen([exe, *args], env=env_for_child(port))
    except OSError as exc:
        report["detail"] = f"could not start {exe}: {exc}"
        return report
    report["launched"] = True
    report["pid"] = int(proc.pid)
    report["detail"] = f"started {exe} with {WEBVIEW2_ARGS_ENV}={debug_args_for(port)}"
    if wait_s > 0:
        deadline = time.time() + wait_s
        while time.time() < deadline:
            if port_is_open(port):
                report["detail"] += f"; DevTools port {port} is open"
                break
            time.sleep(0.5)
        else:
            report["detail"] += f"; DevTools port {port} did not open in {wait_s:.0f}s"
    return report


def port_is_open(port: int = DEFAULT_WEBVIEW_PORT, timeout: float = 2.0) -> bool:
    """Whether something is listening on the DevTools port."""
    import socket
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def list_targets(port: int = DEFAULT_WEBVIEW_PORT) -> list[dict]:
    """The DevTools targets of a web view (usually one page)."""
    url = f"http://127.0.0.1:{port}/json/list"
    request = urllib.request.Request(url, headers={"Host": "127.0.0.1"})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError) as exc:
        raise WebView2Error(
            f"no DevTools endpoint on 127.0.0.1:{port} ({exc}). "
            "Restart the app with WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS="
            f"'{debug_args_for(port)}' to open one."
        ) from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise WebView2Error(
            f"DevTools endpoint on :{port} did not return JSON: {exc}"
        ) from exc


def find_page(port: int = DEFAULT_WEBVIEW_PORT,
              url_contains: str | None = None) -> dict:
    """The page target of a web view."""
    pages = [t for t in list_targets(port) if t.get("type") == "page"]
    if not pages:
        raise WebView2Error(f"no page target on 127.0.0.1:{port}")
    if url_contains:
        for page in pages:
            if url_contains in (page.get("url") or ""):
                return page
    return pages[0]


def describe(port: int = DEFAULT_WEBVIEW_PORT) -> dict:
    """A read-only report about a web view's DevTools endpoint."""
    report: dict = {"port": port, "reachable": False, "pages": [],
                    "detail": ""}
    try:
        targets = list_targets(port)
    except WebView2Error as exc:
        report["detail"] = str(exc)
        return report
    report["reachable"] = True
    report["pages"] = [
        {"id": t.get("id"), "title": t.get("title"), "url": t.get("url"),
         "type": t.get("type")}
        for t in targets
    ]
    report["detail"] = f"{len(report['pages'])} target(s)"
    return report


def main(argv: list[str] | None = None) -> int:
    """CLI: describe a web view's DevTools endpoint, or launch a shell with one."""
    import argparse

    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=DEFAULT_WEBVIEW_PORT)
    parser.add_argument("--launch", default=None,
                        help="path to the desktop shell to start with a "
                             "DevTools port")
    parser.add_argument("--url-contains", default=None)
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    if args.launch:
        print(json.dumps(launch_shell(args.launch, port=args.port),
                         ensure_ascii=False, indent=2))
        return 0

    report = describe(args.port)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["reachable"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
