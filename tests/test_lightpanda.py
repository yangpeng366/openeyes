"""Tests for the Lightpanda CDP backend.

Two layers:

* the failure contract - always runs, and says what went wrong when the
  host cannot start the container;
* the live contract - starts the container, waits for CDP, reads a page
  and stops it again. Skipped when WSL2 + Docker are unavailable.

The live layer is the one that matters: a backend that cannot start a
container must say so, and a backend that claims to work must actually
serve a page.
"""
from __future__ import annotations

import shutil
import socket
import subprocess
import time

import pytest

from openeyes.backends import lightpanda as lpmod

# A WSL cold start is ~50s on this host, but the gate must not hang on it:
# the probe is a readiness check, not a wait-for-success.
_WSL_DOCKER_TIMEOUT_S = 30

pytestmark = pytest.mark.skipif(
    shutil.which("wsl") is None and shutil.which("wsl.exe") is None,
    reason="wsl not on PATH; the Lightpanda backend needs WSL2 + Docker.",
)


def _wsl_docker_ready() -> bool:
    if not lpmod._wsl_available():
        return False
    try:
        proc = subprocess.run(
            ["wsl", "-e", "bash", "-lc", "docker version >/dev/null 2>&1"],
            capture_output=True, text=True, timeout=_WSL_DOCKER_TIMEOUT_S,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_error_is_a_runtime_error():
    """The backend's failure type must be catchable as a RuntimeError."""
    err = lpmod.LightpandaError("boom")
    assert isinstance(err, RuntimeError)
    assert str(err) == "boom"


def test_start_failure_names_the_cause():
    """A host that cannot run the container must say so, not crash opaquely."""
    if _wsl_docker_ready():
        pytest.skip("WSL2 + Docker are available here; the failure path cannot be exercised")
    port = _free_port()
    with pytest.raises(lpmod.LightpandaError) as excinfo:
        lpmod.launch_lightpanda(port=port, wait_s=5.0)
    message = str(excinfo.value).lower()
    assert "docker" in message or "wsl" in message


def test_live_container_serves_cdp_and_a_page():
    """The real contract: the container comes up, CDP answers, a page loads."""
    if not _wsl_docker_ready():
        pytest.skip("WSL2 + Docker are not available on this host")
    from openeyes.backends import cdp

    port = _free_port()
    container = "lightpanda-selftest"
    try:
        info = lpmod.launch_lightpanda(port=port, container=container, wait_s=60.0)
        assert info["backend"] == "lightpanda"
        assert info["port"] == port
        assert info["cdp_version"], "the container must answer /json/version"

        tabs = cdp.list_tabs(port)
        assert tabs, "the container must expose at least one page target"

        conn = cdp.connect(port=port)
        try:
            conn.navigate("data:text/html,<title>lightpanda-selftest</title><p>ok</p>")
            time.sleep(1.0)
            assert conn.evaluate("document.title") == "lightpanda-selftest"
        finally:
            conn.close()
    finally:
        lpmod.stop_lightpanda(container)
def test_a_wsl_timeout_is_reported_as_a_lightpanda_error(monkeypatch):
    """A cold WSL boot must not leak a raw subprocess timeout.

    The contract says a backend that cannot start a container must say so;
    a bare TimeoutExpired says nothing about which layer failed.
    """
    def boom(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd="wsl", timeout=60)

    monkeypatch.setattr(lpmod.subprocess, "run", boom)
    with pytest.raises(lpmod.LightpandaError) as excinfo:
        lpmod.launch_lightpanda(port=_free_port(), wait_s=5.0)
    message = str(excinfo.value).lower()
    assert "wsl" in message or "docker" in message


# ------------------------------------------- wsl.exe output vs the code page
def test_wsl_output_is_utf16le_with_a_bom():
    """The shape wsl.exe actually writes: FF FE, then UTF-16LE characters."""
    raw = b"\xff\xfe" + "docker: not found\r\n".encode("utf-16-le")
    assert lpmod._decode_stream(raw) == "docker: not found\r\n"


def test_utf16le_without_a_bom_is_detected_by_its_nuls():
    """No BOM still means UTF-16LE when every other byte is a NUL."""
    raw = "cannot connect to the daemon".encode("utf-16-le")
    assert lpmod._decode_stream(raw) == "cannot connect to the daemon"


def test_a_childs_utf8_output_is_passed_through():
    """The command's own output is UTF-8; it must not be forced to UTF-16."""
    assert lpmod._decode_stream("docker 守护进程未启动\n".encode()) \
        == "docker 守护进程未启动\n"


def test_undecodable_bytes_become_replacements_not_an_exception():
    """A garbled byte must stay visible; a dead reader loses the whole line."""
    decoded = lpmod._decode_stream(b"\xff\xff\xff\xff")
    assert isinstance(decoded, str)
    assert decoded.count("\ufffd") == 4


def test_empty_output_decodes_to_an_empty_string():
    assert lpmod._decode_stream(None) == ""
    assert lpmod._decode_stream(b"") == ""


def test_live_wsl_output_survives_a_non_utf8_code_page():
    """Live: the real wsl.exe pipe is decoded, not left to cp936."""
    proc = lpmod._run_wsl(["wsl", "-e", "bash", "-lc", "echo openeyes"],
                          timeout=60)
    if proc.returncode != 0:
        pytest.skip(f"wsl -e bash could not run here (rc={proc.returncode})")
    assert "openeyes" in proc.stdout


def test_the_module_never_lets_the_locale_decode_wsl_output():
    """Enforcement: ``text=True`` here is the bug this round removed.

    Parsed, not grepped: the docstring explains the bug by name, and a new
    ``subprocess.run(..., text=True)`` call site must turn this red before a
    dead reader thread can hide a docker error again.
    """
    import ast
    from pathlib import Path

    tree = ast.parse(Path(lpmod.__file__).read_text(encoding="utf-8"))
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(
            func, "id", "")
        if name not in ("run", "Popen", "check_output", "call", "check_call"):
            continue
        for keyword in node.keywords:
            if keyword.arg in ("text", "universal_newlines"):
                offenders.append((node.lineno, keyword.arg))
    assert offenders == [], (
        "wsl output must be decoded by _decode_stream, not by the locale: "
        f"{offenders}"
    )
