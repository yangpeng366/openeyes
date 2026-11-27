"""Verify the OpenEyes MCP stdio handshake without starting a browser.

The probe drives the full JSON-RPC stdio transport end-to-end: it sends
``initialize``, ``notifications/initialized``, ``tools/list``, and three
``tools/call`` requests (``browser_type``, ``browser_shot`` and ``browser_exec``)
in dry-run mode.  The dry-run tool calls exercise the server's tool-dispatch
path over stdio without touching a real browser or the dsh web host,
broadening the repository-local surrogate acceptance while the
127.0.0.1:3080 gate stays closed.

Scope note: this probe issues no anchor call, so its timeout receipt carries no
``roi`` clause.  An ``anchor_wait`` executed with ``roi_pad`` but no captured
frame reports ``roi: null`` (the roll-up is built from the deciding frame), so a
window-free stdio probe cannot produce one.  The clause itself is pinned where
it is produced: ``test_cli_anchor_wait_timeout_text_carries_the_roi_clause``
(text tier) and ``test_anchor_wait_timeout_keeps_the_roi_roll_up`` (MCP
receipt tier).  What this probe owes on a stall is a receipt that names which
requests starved - ``_starved_receipt`` below.
"""

from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
import time
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
    "browser_exec",
    "browser_shot",
    "resolve_elements",
    "vision_detect",
    "app_ensure",
    "click_by_id",
    "act_and_verify",
    "native_surfaces",
    "anchor_probe",
    "anchor_wait",
    "scene_describe",
    "record_replay",
]

# Request id -> verb, so a starved read can name what never answered instead of
# only counting replies.
EXPECTED_REQUESTS = {
    1: "initialize",
    2: "tools/list",
    3: "browser_type",
    4: "browser_shot",
    5: "browser_exec",
}

# One wall-clock budget for the whole reply set, not per read: five 15 s reads
# outlast a caller's own 20 s timeout, so the probe would be killed before it
# could print the receipt that says why.
REPLY_BUDGET_S = 15.0

PROBE_TEXT = "acceptance"
PROBE_CODE = "document.title"
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


def _starved_receipt(
    messages: list[dict],
    *,
    budget: float = REPLY_BUDGET_S,
    stderr: str = "",
) -> dict:
    """The receipt for a read that ran out of time: which verbs never answered.

    An empty ``str(exc)`` used to make a starved read look like a silent
    failure, and the count alone (``2/4``) named neither the missing verbs nor
    the right total. The receipt now lists every request that never replied by
    verb, and reports the count against the number actually sent.
    """
    seen = {message.get("id") for message in messages}
    missing = [rid for rid in EXPECTED_REQUESTS if rid not in seen]
    named = ", ".join(f"{EXPECTED_REQUESTS[rid]} (id {rid})" for rid in missing)
    return {
        "ready": False,
        "error": (
            f"no reply to {named or 'any request'} within {budget:g}s "
            f"({len(messages)}/{len(EXPECTED_REQUESTS)} replies received)"
        ),
        "error_type": "TimeoutError",
        "missing": missing,
        "stderr": stderr[-2000:],
    }


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
            _request(
                5,
                "tools/call",
                {
                    "name": "browser_exec",
                    "arguments": {"code": PROBE_CODE, "dry_run": True},
                },
            ),
        ]
    ) + "\n"

    try:
        assert process.stdin is not None
        process.stdin.write(payload)
        process.stdin.flush()
        messages = []
        deadline = time.monotonic() + REPLY_BUDGET_S
        while len(messages) < len(EXPECTED_REQUESTS):
            remaining = deadline - time.monotonic()
            line = None
            if remaining > 0:
                try:
                    line = lines.get(timeout=remaining)
                except queue.Empty:
                    line = None
            if line is None:
                print(json.dumps(
                    _starved_receipt(messages, budget=REPLY_BUDGET_S,
                                     stderr=_stop_process(process)),
                    ensure_ascii=False,
                ))
                return 2
            message = json.loads(line)
            if message.get("id") in EXPECTED_REQUESTS:
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
        exec_resp = next(message for message in messages if message.get("id") == 5)
        for resp, label in ((type_resp, "browser_type"), (shot_resp, "browser_shot"),
                           (exec_resp, "browser_exec")):
            if "error" in resp:
                raise RuntimeError(f"{label} tools/call failed: {resp['error']}")
        type_result = json.loads(type_resp["result"]["content"][0]["text"])
        shot_result = json.loads(shot_resp["result"]["content"][0]["text"])
        exec_result = json.loads(exec_resp["result"]["content"][0]["text"])
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
        if exec_result.get("executed") is not False:
            raise RuntimeError(f"browser_exec dry-run executed should be False: {exec_result}")
        if exec_result.get("dry_run") is not True:
            raise RuntimeError(f"browser_exec dry_run should be True: {exec_result}")
        if exec_result.get("would_execute") is not True:
            raise RuntimeError(f"browser_exec would_execute should be True: {exec_result}")
    except (AssertionError, OSError, queue.Empty, TypeError, ValueError, KeyError, RuntimeError) as exc:
        stderr = _stop_process(process)
        # Some of these carry an empty ``str()`` (bare ``OSError``), and an empty
        # error string is indistinguishable from no diagnostic at all, so the
        # class name travels with it.
        print(json.dumps({
            "ready": False,
            "error": str(exc) or type(exc).__name__,
            "error_type": type(exc).__name__,
            "stderr": stderr[-2000:],
        }))
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
                    "browser_exec": {
                        "executed": exec_result["executed"],
                        "dry_run": exec_result["dry_run"],
                        "would_execute": exec_result["would_execute"],
                    },
                },
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
