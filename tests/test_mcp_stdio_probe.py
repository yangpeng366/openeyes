import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


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
    assert payload["tool_count"] == 13