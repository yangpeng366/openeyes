"""Tests for the Lightpanda CDP backend. Skipped when WSL+Docker are unavailable."""
from __future__ import annotations

import shutil
import subprocess

import pytest

from openeyes.backends import lightpanda as lpmod


# WSL2 cold start is ~50s on this host; the readiness probe must allow it.
_WSL_DOCKER_TIMEOUT_S = 90


def _wsl_docker_ready() -> bool:
    if shutil.which("wsl") is None and shutil.which("wsl.exe") is None:
        return False
    try:
        proc = subprocess.run(
            ["wsl", "-e", "bash", "-lc", "docker version >/dev/null 2>&1"],
            capture_output=True, text=True, timeout=_WSL_DOCKER_TIMEOUT_S,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


pytestmark = pytest.mark.skipif(
    not _wsl_docker_ready(),
    reason="WSL2 + Docker not available; Lightpanda backend needs both.",
)


def test_wsl_probe_does_not_raise():
    """`_wsl_available` must return a clean bool; never throw on this host."""
    assert isinstance(lpmod._wsl_available(), bool)


def test_lightpanda_error_is_runtime():
    err = lpmod.LightpandaError("boom")
    assert isinstance(err, RuntimeError)
    assert str(err) == "boom"