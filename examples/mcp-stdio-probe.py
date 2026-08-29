"""Verify the OpenEyes MCP stdio handshake without starting a browser.

The probe drives the full JSON-RPC stdio transport end-to-end: it sends
``initialize``, ``notifications/initialized``, ``tools/list``, and two
``tools/call`` requests (``browser_type`` and ``browser_shot``) in dry-run
mode.  The dry-run tool calls exercise the server's tool-dispatch path over
stdio without touching a real browser or the dsh web host, broadening the
repository-local surrogate acceptance while the 127.0.0.1:3080 gate stays
closed.
"""

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

PROBE_TEXT = "acceptance"
PROBE_SHOT_PATH = "shots/stdio-acceptance.png"


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
            _request(
                3,
                "tools/call",
                {
                    "name": "browser_type",
                    "arguments": {"text": PROBE_TEXT, "dry_run": True},
                },
            ),
            _request(
                4,
                "tools/call",
                {
                    "name": "browser_shot",
                    "arguments": {"out": PROBE_SHOT_PATH, "dry_run": True},
                },
            ),
        ]
    ) + "\n"

    try:
        assert process.stdin is not None
        process.stdin.write(payload)
        process.stdin.flush()
        messages = []
        deadline = 15
        while len(messages) < 4:
            line = lines.get(timeout=deadline)
            message = json.loads(line)
            if message.get("id") in (1, 2, 3, 4):
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
        type_resp = next(message for message in messages if message.get("id") == 3)
        shot_resp = next(message for message in messages if message.get("id") == 4)
        for resp, label in ((type_resp, "browser_type"), (shot_resp, "browser_shot")):
            if "error" in resp:
                raise RuntimeError(f"{label} tools/call failed: {resp['error']}")
        type_result = json.loads(type_resp["result"]["content"][0]["text"])
        shot_result = json.loads(shot_resp["result"]["content"][0]["text"])
        if type_result.get("sent") is not False:
            raise RuntimeError(f"browser_type dry-run sent should be False: {type_result}")
        if type_result.get("dry_run") is not True:
            raise RuntimeError(f"browser_type dry_run should be True: {type_result}")
        if type_result.get("would_send_chars") != len(PROBE_TEXT):
            raise RuntimeError(
                f"browser_type would_send_chars mismatch: {type_result}"
            )
        if shot_result.get("captured") is not False:
            raise RuntimeError(f"browser_shot dry-run captured should be False: {shot_result}")
        if shot_result.get("dry_run") is not True:
            raise RuntimeError(f"browser_shot dry_run should be True: {shot_result}")
        if shot_result.get("path") != PROBE_SHOT_PATH:
            raise RuntimeError(f"browser_shot path mismatch: {shot_result}")
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
                "tool_calls": {
                    "browser_type": {
                        "sent": type_result["sent"],
                        "dry_run": type_result["dry_run"],
                        "would_send_chars": type_result["would_send_chars"],
                    },
                    "browser_shot": {
                        "captured": shot_result["captured"],
                        "dry_run": shot_result["dry_run"],
                        "path": shot_result["path"],
                    },
                },
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())