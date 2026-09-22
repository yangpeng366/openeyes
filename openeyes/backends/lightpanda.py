# coding: utf-8
"""Lightpanda CDP backend for OpenEyes.

Lightpanda is an AI-native headless browser written in Zig with a minimal
memory footprint (about 123 MB peak for 100 pages, vs about 2 GB for
Chrome headless). It speaks Chrome DevTools Protocol on port 9222 and is
binary-compatible with Puppeteer / Playwright clients.

This module mirrors the surface of ``openeyes.backends.cdp.launch_edge``
just enough to be selectable from ``eyes browser launch --backend lightpanda``
and from the eyes-mcp ``browser_launch`` tool. The DOM probe, click_center,
type_text, navigate, and scroll paths are unchanged - they already talk CDP
through ``openeyes.backends.cdp.connect``.

Hard-won facts (this machine):
- WSL2 + Docker 29.4.3 already installed. Lightpanda ships as
  ``lightpanda/browser:nightly`` (Docker) or a single Linux binary.
- WSL forwards ``localhost:9222`` to the Windows host automatically, so the
  OpenEyes CDP client keeps using ``127.0.0.1:9222``.
- Each Lightpanda session has its own cookies / no persistent profile.
  Do NOT use this backend for login flows that need cross-launch cookies.
- Lightpanda has no graphical rendering backend. ``Page.captureScreenshot``
  returns an empty PNG. ``eyes browser shot`` will not produce a usable image.

Run from PowerShell on this Windows host::

    wsl -e docker run -d --name lightpanda --rm -p 127.0.0.1:9222:9222 lightpanda/browser:nightly

The code below wraps that in a Python entry point so the CLI / MCP clients
do not have to shell out themselves.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from typing import Any

from openeyes.backends.cdp import CDP_DEFAULT_PORT, CDP_LOCALHOST, list_tabs


LIGHTPANDA_IMAGE = "lightpanda/browser:nightly"
LIGHTPANDA_CONTAINER = "lightpanda"


class LightpandaError(RuntimeError):
    """Raised when Lightpanda cannot be started or reached."""


def _wsl_available() -> bool:
    """Return True if wsl.exe is on PATH and a default distro is registered."""
    if shutil.which("wsl") is None and shutil.which("wsl.exe") is None:
        return False
    try:
        proc = subprocess.run(
            ["wsl", "-e", "bash", "-lc", "true"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def _wsl_docker_pull(image: str) -> None:
    """Pull the Lightpanda Docker image inside WSL (best-effort, quiet)."""
    subprocess.run(
        ["wsl", "-e", "bash", "-lc", f"docker pull {image}"],
        capture_output=True, text=True, timeout=180,
    )


def _wsl_docker_run(container: str, image: str, port: int) -> str:
    """Start (or restart) the Lightpanda container; return the docker run stdout."""
    # Stop a previous container with the same name first so we always bind fresh.
    subprocess.run(
        ["wsl", "-e", "bash", "-lc", f"docker rm -f {container} 2>/dev/null; true"],
        capture_output=True, text=True, timeout=20,
    )
    proc = subprocess.run(
        [
            "wsl", "-e", "bash", "-lc",
            f"docker run -d --name {container} --rm -p 127.0.0.1:{port}:{port} {image}",
        ],
        capture_output=True, text=True, timeout=60,
    )
    if proc.returncode != 0:
        raise LightpandaError(
            "docker run failed (rc=" + str(proc.returncode) + "): "
            + (proc.stderr.strip() or proc.stdout.strip())
        )
    return proc.stdout.strip()


def _wait_for_cdp(port: int, timeout_s: float) -> dict[str, Any]:
    """Poll http://127.0.0.1:<port>/json/version until it responds."""
    url = "http://" + CDP_LOCALHOST + ":" + str(port) + "/json/version"
    deadline = time.time() + timeout_s
    last_err: Exception | None = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as e:
            last_err = e
            time.sleep(0.5)
    raise LightpandaError(
        "Lightpanda CDP did not come up on " + url + " within "
        + str(int(timeout_s)) + "s: " + repr(last_err)
    )


def launch_lightpanda(
    *,
    port: int = CDP_DEFAULT_PORT,
    image: str = LIGHTPANDA_IMAGE,
    container: str = LIGHTPANDA_CONTAINER,
    url: str | None = None,
    pull: bool = False,
    wait_s: float = 30.0,
) -> dict[str, Any]:
    """Start a Lightpanda container in WSL and return when CDP is reachable.

    Returns a dict with ``port``, ``container``, ``image``, and ``cdp_version``
    so the CLI / MCP clients can echo it. Does NOT open a tab; call
    ``openeyes.backends.cdp.connect(port=...)`` afterwards to attach.
    """
    if not _wsl_available():
        raise LightpandaError(
            "wsl.exe is not available or no default distro is registered; "
            "Lightpanda backend requires WSL2 + Docker on this host."
        )
    if pull:
        _wsl_docker_pull(image)
    _wsl_docker_run(container, image, port)
    version = _wait_for_cdp(port, wait_s)
    info: dict[str, Any] = {
        "backend": "lightpanda",
        "port": port,
        "container": container,
        "image": image,
        "cdp_version": version,
    }
    # Best-effort: confirm a page target exists (Lightpanda exposes one tab).
    try:
        tabs = list_tabs(port)
        info["tabs"] = len(tabs)
    except Exception:
        info["tabs"] = 0
    if url:
        # Caller asked for an initial URL; navigate via the first tab.
        from openeyes.backends import cdp as _cdp  # local import to avoid cycles
        conn = _cdp.connect(port=port)
        try:
            conn.navigate(url)
        finally:
            try:
                conn.close()
            except Exception:
                pass
    return info


def stop_lightpanda(container: str = LIGHTPANDA_CONTAINER) -> bool:
    """Stop the Lightpanda container. Returns True if docker rm reported success."""
    if not _wsl_available():
        return False
    proc = subprocess.run(
        ["wsl", "-e", "bash", "-lc", f"docker rm -f {container} 2>/dev/null"],
        capture_output=True, text=True, timeout=20,
    )
    return proc.returncode == 0