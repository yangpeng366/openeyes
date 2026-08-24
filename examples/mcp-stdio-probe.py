"""Verify the OpenEyes MCP stdio handshake without starting a browser."""

from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
from pathlib import Path


EXPECTED_TOOLS = [
    "list_windows",
    "capture_window",
    "detect_elements",
    "click",
    "grid",
    "hotkey",
    "type_text",
    "browser_launch",
    "browser_tabs",
    "browser_scan",
    "browser_click",
    "browser_type",
    "browser_shot",
]


def _request(request_id: int, method: str, params: dict | None = None) -> str:
    message = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        message["params"] = params
    return json.dumps(message, ensure_ascii=False)


def _notification(method: str, params: dict | None = None) -> str:
    message = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        message["params"] = params
    return json.dumps(message, ensure_ascii=False)


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


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    process = subprocess.Popen(
        [sys.executable, "-m", "openeyes.mcp.server"],
        cwd=repo_root,
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
    payload = "\n".join(
        [
            _request(
                1,
                "initialize",
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "openeyes-stdio-probe", "version": "1"},
                },
            ),
            _notification("notifications/initialized"),
            _request(2, "tools/list"),
        ]
    ) + "\n"

    try:
        assert process.stdin is not None
        process.stdin.write(payload)
        process.stdin.flush()
        messages = []
        deadline = 15
        while len(messages) < 2:
            line = lines.get(timeout=deadline)
            message = json.loads(line)
            if message.get("id") in (1, 2):
                messages.append(message)
        initialize = next(message for message in messages if message.get("id") == 1)
        tools_list = next(message for message in messages if message.get("id") == 2)
        if "error" in initialize:
            raise RuntimeError(f"initialize failed: {initialize['error']}")
        if "error" in tools_list:
            raise RuntimeError(f"tools/list failed: {tools_list['error']}")
        tool_names = [tool["name"] for tool in tools_list["result"]["tools"]]
        if tool_names != EXPECTED_TOOLS:
            raise RuntimeError(
                f"unexpected tools/list names: expected {EXPECTED_TOOLS!r}, got {tool_names!r}"
            )
    except (AssertionError, OSError, queue.Empty, TypeError, ValueError, KeyError, RuntimeError) as exc:
        stderr = _stop_process(process)
        print(json.dumps({"ready": False, "error": str(exc), "stderr": stderr[-2000:]}))
        return 2

    _stop_process(process)
    print(
        json.dumps(
            {
                "ready": True,
                "protocol": "stdio",
                "tool_count": len(tool_names),
                "tool_names": tool_names,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())