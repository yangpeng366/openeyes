"""Contract tests for examples/launch-debug-edge.ps1.

The helper is a PowerShell wrapper that:

* if :<Port> is already LISTEN, runs `eyes browser tabs` and exits
* otherwise calls `eyes browser launch --port <Port>` and waits
* can also `-Stop` the msedge.exe it previously launched
* can be invoked read-only via `-Help`

These tests only exercise the read-only and no-op paths so they are
safe to run inside a patrol round (they never spawn Edge).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "examples" / "launch-debug-edge.ps1"


def _pwsh() -> str | None:
    return shutil.which("pwsh") or shutil.which("powershell")


pytestmark = pytest.mark.skipif(_pwsh() is None, reason="pwsh not on PATH")


def _run(args: list[str], timeout: int = 30) -> subprocess.CompletedProcess[str]:
    assert SCRIPT.exists(), f"missing helper script at {SCRIPT}"
    cmd = [_pwsh(), "-NoLogo", "-NoProfile", "-File", str(SCRIPT), *args]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=str(ROOT))


def test_helper_script_is_no_bom_utf8_and_readable():
    raw = SCRIPT.read_bytes()
    assert raw[:3] != b"\xef\xbb\xbf", "launch-debug-edge.ps1 must not start with a UTF-8 BOM"
    text = SCRIPT.read_text(encoding="utf-8")
    assert "param(" in text, "expected a PowerShell param() block"
    assert "[int]$Port = 9222" in text
    assert "[switch]$Stop" in text
    assert "[switch]$Help" in text
    assert "function Test-PortOpen" in text
    assert "eyes browser tabs" in text
    assert "eyes browser launch" in text
    for code in (0, 2, 3, 4):
        assert f"exit {code}" in text, f"missing documented exit code {code}"


def test_helper_help_exits_zero_and_prints_usage():
    proc = _run(["-Help"])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "OpenEyes Debug-Edge launcher" in proc.stdout
    assert "-Port" in proc.stdout
    assert "-Stop" in proc.stdout
    assert "Exit codes" in proc.stdout or "退出码" in proc.stdout


def test_helper_stop_with_unused_port_is_noop():
    # Port 19999 is unlikely to host any debug Edge; the -Stop branch
    # should report nothing to stop and exit cleanly.
    proc = _run(["-Stop", "-Port", "19999"])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "nothing to stop" in proc.stdout or "no msedge.exe" in proc.stdout