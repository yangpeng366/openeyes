"""Run the two-tab browser_click dry-run acceptance over direct MCP stdio.

This probe intentionally bypasses the dsh web client so repository-local MCP
dispatch can be accepted independently while the web-host gate remains closed.
Open the fixture tabs with ``open-acceptance-tabs.py --go`` first.
"""

from __future__ import annotations

import argparse
import json
import queue
import subprocess
import sys
import threading
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
ACCEPTANCE_MARKER = "acceptance-pages"
TARGET_MARKER = "target-a"


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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cdp-port", type=int, default=9222)
    parser.add_argument("--timeout", type=float, default=20.0)
    args = parser.parse_args()

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
        call(
            1,
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "openeyes-browser-click-acceptance",
                    "version": "1",
                },
            },
        )
        assert process.stdin is not None
        process.stdin.write(_notification("notifications/initialized") + "\n")
        process.stdin.flush()

        tools = call(2, "tools/list")["tools"]
        tool_names = [tool["name"] for tool in tools]
        if "browser_tabs" not in tool_names or "browser_click" not in tool_names:
            raise RuntimeError(
                "MCP surface does not expose browser_tabs/browser_click"
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
                "tab; "
                f"got targets={len(target_tabs)}, decoys={len(decoy_tabs)}, "
                f"tabs={tabs!r}"
            )
        target_tabs = target_tabs[:1]
        decoy_tabs = decoy_tabs[:1]

        matched = _result_content(call(4, "tools/call", {
            "name": "browser_click",
            "arguments": {
                "port": args.cdp_port,
                "url_contains": TARGET_MARKER,
                "name_contains": "Learn more",
            },
        }))
        if matched.get("clicked") is not False or matched.get("would_click") is not True:
            raise RuntimeError(f"matched call was not a dry-run: {matched!r}")
        if matched.get("target", {}).get("name") != "Learn more":
            raise RuntimeError(f"matched call resolved wrong element: {matched!r}")

        missing = _result_content(call(5, "tools/call", {
            "name": "browser_click",
            "arguments": {
                "port": args.cdp_port,
                "url_contains": "missing-target",
                "name_contains": "Learn more",
            },
        }))
        error = missing.get("error", "")
        if "no page target matched url_contains" not in error:
            raise RuntimeError(f"missing call did not fail closed: {missing!r}")
        if missing.get("would_click") or missing.get("target"):
            raise RuntimeError(f"missing call proposed an action: {missing!r}")
    except (AssertionError, KeyError, OSError, queue.Empty, RuntimeError, TypeError,
            ValueError, json.JSONDecodeError) as exc:
        stderr = _stop_process(process)
        print(json.dumps({
            "passed": False,
            "error": str(exc),
            "stderr": stderr[-2000:],
        }, ensure_ascii=False))
        return 2

    _stop_process(process)
    print(json.dumps({
        "passed": True,
        "transport": "mcp-stdio",
        "acceptance_tabs": len(acceptance_tabs),
        "target_url": target_tabs[0]["url"],
        "matched_click": matched,
        "missing_click_error": error,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
