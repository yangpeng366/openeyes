"""Run the two-tab browser_shot dry-run acceptance over direct MCP stdio.

This probe broadens the repository-local surrogate so the ``browser_shot``
write contract also accepts the URL selector while the dsh web-host gate
remains closed. It starts a transient HTTP fixture server, opens target and
decoy tabs through CDP, then performs both checks over MCP stdio.

The matched dry-run call must resolve ``target-a`` and report its URL without
creating a PNG. The unmatched ``url_contains`` call must fail closed with
``no page target matched url_contains`` and must not report ``target_url``.
"""

from __future__ import annotations

import argparse
import functools
import http.server
import json
import queue
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLES_DIR = REPO_ROOT / "examples"
ACCEPTANCE_MARKER = "acceptance-pages"
TARGET_MARKER = "target-a"
SHOT_PATH = "shots/browser-shot-acceptance.png"


def _request(request_id: int, method: str, params: dict | None = None) -> str:
    message = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        message["params"] = params
    return json.dumps(message, ensure_ascii=False)


def _notification(method: str) -> str:
    return json.dumps(
        {"jsonrpc": "2.0", "method": method}, ensure_ascii=False
    )


def _read_stdout(stream, lines: queue.Queue[str]) -> None:
    for line in stream:
        if line.strip():
            lines.put(line)


def _stop_process(process: subprocess.Popen[str]) -> str:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    return process.stderr.read() if process.stderr else ""


def _result_content(result: dict) -> dict:
    content = result["content"]
    if len(content) != 1 or content[0].get("type") != "text":
        raise RuntimeError(f"unexpected tool result content: {content!r}")
    return json.loads(content[0]["text"])


def _port_listening(port: int, host: str = "127.0.0.1",
                    timeout: float = 2.0) -> bool:
    import socket
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _start_fixture_server(
    directory: Path,
) -> tuple[http.server.ThreadingHTTPServer, int]:
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(directory)
    )
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, server.server_address[1]


def _open_tab(cdp_port: int, url: str) -> str:
    encoded = urllib.parse.quote(url, safe="")
    req = urllib.request.Request(
        f"http://127.0.0.1:{cdp_port}/json/new?{encoded}",
        method="PUT",
        headers={"Host": "127.0.0.1"},
    )
    with urllib.request.urlopen(req, timeout=10) as response:
        raw = response.read().decode("utf-8", errors="replace")
    return json.loads(raw).get("id", "") if raw.strip() else ""


def _close_tab(cdp_port: int, target_id: str) -> None:
    if not target_id:
        return
    req = urllib.request.Request(
        f"http://127.0.0.1:{cdp_port}/json/close/{target_id}",
        headers={"Host": "127.0.0.1"},
    )
    try:
        urllib.request.urlopen(req, timeout=5).read()
    except Exception:
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cdp-port", type=int, default=9222)
    parser.add_argument("--timeout", type=float, default=20.0)
    args = parser.parse_args()

    fixture_server = None
    opened_ids: list[str] = []
    process = subprocess.Popen(
        [sys.executable, "-m", "openeyes.mcp.server"],
        cwd=REPO_ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    lines: queue.Queue[str] = queue.Queue()
    reader = threading.Thread(
        target=_read_stdout,
        args=(process.stdout, lines),
        daemon=True,
    )
    reader.start()

    def call(request_id: int, method: str, params: dict | None = None) -> dict:
        assert process.stdin is not None
        process.stdin.write(_request(request_id, method, params) + "\n")
        process.stdin.flush()
        while True:
            message = json.loads(lines.get(timeout=args.timeout))
            if message.get("id") != request_id:
                continue
            if "error" in message:
                raise RuntimeError(f"{method} failed: {message['error']}")
            if "result" not in message:
                raise RuntimeError(f"{method} returned no result: {message!r}")
            return message["result"]

    try:
        if not _port_listening(args.cdp_port):
            raise RuntimeError(
                f"debug port {args.cdp_port} not listening - "
                f"start Edge with --remote-debugging-port={args.cdp_port}"
            )
        fixture_server, http_port = _start_fixture_server(EXAMPLES_DIR)
        target_url = (
            f"http://127.0.0.1:{http_port}/acceptance-pages/target-a.html"
        )
        decoy_url = (
            f"http://127.0.0.1:{http_port}/acceptance-pages/decoy.html"
        )
        opened_ids.append(_open_tab(args.cdp_port, target_url))
        opened_ids.append(_open_tab(args.cdp_port, decoy_url))
        time.sleep(0.8)

        call(
            1,
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "openeyes-browser-shot-acceptance",
                    "version": "1",
                },
            },
        )
        assert process.stdin is not None
        process.stdin.write(_notification("notifications/initialized") + "\n")
        process.stdin.flush()

        tools = call(2, "tools/list")["tools"]
        tool_names = [tool["name"] for tool in tools]
        if "browser_tabs" not in tool_names or "browser_shot" not in tool_names:
            raise RuntimeError(
                "MCP surface does not expose browser_tabs/browser_shot"
            )

        tabs = _result_content(call(3, "tools/call", {
            "name": "browser_tabs",
            "arguments": {"port": args.cdp_port},
        }))
        acceptance_tabs = [
            tab for tab in tabs if ACCEPTANCE_MARKER in (tab.get("url") or "")
        ]
        target_tabs = [
            tab for tab in acceptance_tabs
            if TARGET_MARKER in (tab.get("url") or "")
        ]
        decoy_tabs = [
            tab for tab in acceptance_tabs
            if TARGET_MARKER not in (tab.get("url") or "")
        ]
        if len(target_tabs) < 1 or len(decoy_tabs) < 1:
            raise RuntimeError(
                "expected at least one target-a and one decoy acceptance "
                f"tab; got targets={len(target_tabs)}, "
                f"decoys={len(decoy_tabs)}, tabs={tabs!r}"
            )
        target_tabs = target_tabs[:1]
        decoy_tabs = decoy_tabs[:1]

        matched = _result_content(call(4, "tools/call", {
            "name": "browser_shot",
            "arguments": {
                "port": args.cdp_port,
                "out": SHOT_PATH,
                "url_contains": TARGET_MARKER,
            },
        }))
        if matched.get("captured") is not False:
            raise RuntimeError(f"matched call was not a dry-run: {matched!r}")
        if matched.get("path") != SHOT_PATH:
            raise RuntimeError(f"matched call echoed the wrong path: {matched!r}")
        if matched.get("target_url") != target_tabs[0]["url"]:
            raise RuntimeError(
                f"matched call resolved the wrong target: {matched!r}"
            )

        missing = _result_content(call(5, "tools/call", {
            "name": "browser_shot",
            "arguments": {
                "port": args.cdp_port,
                "out": SHOT_PATH,
                "url_contains": "missing-target",
            },
        }))
        error = missing.get("error", "")
        if "no page target matched url_contains" not in error:
            raise RuntimeError(f"missing call did not fail closed: {missing!r}")
        if missing.get("target_url") or missing.get("path"):
            raise RuntimeError(f"missing call proposed a target: {missing!r}")
    except (AssertionError, KeyError, OSError, queue.Empty, RuntimeError,
            TypeError, ValueError, json.JSONDecodeError) as exc:
        stderr = _stop_process(process)
        print(json.dumps({
            "passed": False,
            "error": str(exc),
            "stderr": stderr[-2000:],
        }, ensure_ascii=False))
        return 2
    finally:
        for target_id in opened_ids:
            _close_tab(args.cdp_port, target_id)
        if fixture_server is not None:
            fixture_server.shutdown()

    _stop_process(process)
    print(json.dumps({
        "passed": True,
        "transport": "mcp-stdio",
        "acceptance_tabs": len(acceptance_tabs),
        "target_url": target_tabs[0]["url"],
        "matched_shot": matched,
        "missing_shot_error": error,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())