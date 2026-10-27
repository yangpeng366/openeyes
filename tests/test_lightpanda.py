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
