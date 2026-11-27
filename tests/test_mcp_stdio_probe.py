import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "examples" / "mcp-stdio-probe.py"


def _probe_module():
    spec = importlib.util.spec_from_file_location("mcp_stdio_probe", PROBE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_mcp_stdio_probe_passes():
    result = subprocess.run(
        [sys.executable, "examples/mcp-stdio-probe.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout)
    assert payload["ready"] is True
    assert payload["tool_count"] == 24

    type_call = payload["tool_calls"]["browser_type"]
    assert type_call == {
        "sent": False,
        "dry_run": True,
        "would_send_chars": len("acceptance"),
    }

    shot_call = payload["tool_calls"]["browser_shot"]
    assert shot_call == {
        "captured": False,
        "dry_run": True,
        "path": "shots/stdio-acceptance.png",
    }

    exec_call = payload["tool_calls"]["browser_exec"]
    assert exec_call == {
        "executed": False,
        "dry_run": True,
        "would_execute": True,
    }


def test_starved_receipt_names_the_verbs_that_never_answered():
    """A stall says which requests starved, and counts against what was sent.

    The receipt used to read ``no reply to request id(s) [3, 4] within 15s
    (2/4 replies received)``: a caller had to map ids back to verbs, and the
    denominator said 4 while five requests were written. Both are pinned here
    on the receipt builder, so the diagnostic survives without a stalled
    server.
    """
    module = _probe_module()
    answered = [{"jsonrpc": "2.0", "id": 1, "result": {}},
                {"jsonrpc": "2.0", "id": 2, "result": {"tools": []}}]

    receipt = module._starved_receipt(answered, budget=15.0, stderr="")

    assert receipt["ready"] is False
    assert receipt["error_type"] == "TimeoutError"
    assert receipt["missing"] == [3, 4, 5]
    assert receipt["error"] == (
        "no reply to browser_type (id 3), browser_shot (id 4), "
        "browser_exec (id 5) within 15s (2/5 replies received)"
    )


def test_starved_receipt_is_empty_of_verbs_when_nothing_replied():
    """Nothing received names no verb, but still reports 0 of the real total."""
    module = _probe_module()

    receipt = module._starved_receipt([], budget=15.0, stderr="boom")

    assert receipt["missing"] == [1, 2, 3, 4, 5]
    assert "replies received" in receipt["error"]
    assert receipt["error"].endswith("(0/5 replies received)")
    assert receipt["stderr"] == "boom"


def test_probe_request_ids_cover_every_expected_verb():
    """The receipt's id table is exactly the ids the probe writes into the pipe."""
    module = _probe_module()

    assert module.EXPECTED_REQUESTS == {
        1: "initialize",
        2: "tools/list",
        3: "browser_type",
        4: "browser_shot",
        5: "browser_exec",
    }
    written = sorted(int(match) for match in re.findall(
        r"_request\(\s*(\d+),", PROBE.read_text(encoding="utf-8")))
    assert written == sorted(module.EXPECTED_REQUESTS)
