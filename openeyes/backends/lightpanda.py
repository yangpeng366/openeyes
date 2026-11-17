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

Prerequisites on this host:
- WSL2 with a **running Docker daemon inside the distro**. The daemon does
  not always come back after a WSL restart: ``wsl -e bash -lc "docker
  version"`` is the check and ``wsl -e bash -lc "sudo systemctl start
  docker"`` is the fix. When the daemon is down, ``launch_lightpanda``
  raises ``LightpandaError`` naming that cause instead of hanging.
- Lightpanda ships as ``lightpanda/browser:nightly`` (Docker) or a single
  Linux binary.
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

import contextlib
import json
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


def _decode_stream(raw: bytes | None) -> str:
    """Decode what ``wsl.exe`` wrote, without trusting the ANSI code page.

    ``wsl.exe`` writes **UTF-16LE** (usually with a ``FF FE`` BOM) into a
    pipe, while the output of the command it runs is passed through as
    UTF-8. ``text=True`` would decode both with the ANSI code page - cp936
    on this host - and on a byte it cannot map the reader thread raises
    :class:`UnicodeDecodeError`, so the very error line a failed ``docker
    run`` needs disappears (the gate saw this as a dead reader thread). The
    bytes are therefore decoded here, explicitly:

    * ``FF FE`` / ``FE FF`` - UTF-16LE / UTF-16BE, BOM dropped;
    * NUL-heavy payload - UTF-16LE without a BOM (the ``,\x00k\x00`` tell);
    * anything else - UTF-8, which is what a child command emits.

    ``errors="replace"`` keeps a garbled byte a visible ``\ufffd`` instead
    of an exception, because a failure message that cannot be read is not a
    failure message.
    """
    if not raw:
        return ""
    if raw.startswith(b"\xff\xfe"):
        return raw[2:].decode("utf-16-le", errors="replace")
    if raw.startswith(b"\xfe\xff"):
        return raw[2:].decode("utf-16-be", errors="replace")
    head = raw[:64]
    if b"\x00" in head and head.count(b"\x00") * 4 >= len(head):
        return raw.decode("utf-16-le", errors="replace")
    return raw.decode("utf-8", errors="replace")


def _run_wsl(args: list[str], *, timeout: float) -> subprocess.CompletedProcess:
    """Run ``wsl.exe`` and hand back text the locale never touched.

    Every call in this module goes through here: bytes out of the pipe, one
    explicit decode, then ``stdout`` / ``stderr`` as ``str`` exactly like
    ``text=True`` would have produced - minus the crash.
    """
    proc = subprocess.run(args, capture_output=True, timeout=timeout)
    return subprocess.CompletedProcess(
        args=proc.args,
        returncode=proc.returncode,
        stdout=_decode_stream(proc.stdout),
        stderr=_decode_stream(proc.stderr),
    )


def _wsl_available() -> bool:
    """Return True if wsl.exe is on PATH and a default distro is registered."""
    if shutil.which("wsl") is None and shutil.which("wsl.exe") is None:
        return False
    try:
        proc = _run_wsl(["wsl", "-e", "bash", "-lc", "true"], timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def _wsl_docker_pull(image: str) -> None:
    """Pull the Lightpanda Docker image inside WSL (best-effort, quiet)."""
    _run_wsl(["wsl", "-e", "bash", "-lc", f"docker pull {image}"],
             timeout=180)


def _require_wsl_docker() -> None:
    """Fail fast, and with a named cause, when the docker daemon is not up.

    A cold WSL boot costs tens of seconds, and a `docker run` against a
    dead daemon would burn that time twice (once for the cleanup, once for
    the run) before reporting anything. Probing first turns a slow,
    opaque failure into one fast, named one.
    """
    try:
        probe = _run_wsl(
            ["wsl", "-e", "bash", "-lc", "docker version >/dev/null 2>&1"],
            timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        raise LightpandaError(
            "the docker daemon inside WSL did not answer within 30s: "
            "wsl -e bash -lc 'docker version'"
        ) from exc
    except OSError as exc:
        raise LightpandaError(
            f"could not reach WSL: {exc}"
        ) from exc
    if probe.returncode != 0:
        raise LightpandaError(
            "the docker daemon inside WSL is not reachable: "
            "wsl -e bash -lc 'sudo systemctl start docker'"
        )


def _wsl_docker_run(container: str, image: str, port: int) -> str:
    """Start (or restart) the Lightpanda container; return the docker run stdout."""
    _require_wsl_docker()
    # Stop a previous container with the same name first so we always bind
    # fresh. This cleanup is best-effort: a cold WSL boot can outlast the
    # timeout, and a stale container is caught by the `docker run` below,
    # which reports the real cause.
    with contextlib.suppress(OSError, subprocess.TimeoutExpired):
        _run_wsl(
            ["wsl", "-e", "bash", "-lc",
             f"docker rm -f {container} 2>/dev/null; true"],
            timeout=20,
        )
    try:
        proc = _run_wsl(
            [
                "wsl", "-e", "bash", "-lc",
                f"docker run -d --name {container} --rm "
                f"-p 127.0.0.1:{port}:{port} {image}",
            ],
            timeout=60,
        )
    except subprocess.TimeoutExpired as exc:
        raise LightpandaError(
            "docker run timed out inside WSL after "
            f"{exc.timeout}s (the WSL distro or the docker daemon may be "
            "cold or unreachable): wsl -e bash -lc 'docker version'"
        ) from exc
    except OSError as exc:
        raise LightpandaError(
            f"could not run docker through WSL: {exc}"
        ) from exc
    if proc.returncode != 0:
        raise LightpandaError(
            "docker run failed (rc=" + str(proc.returncode) + "): " +
            (proc.stderr.strip() or proc.stdout.strip())
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
            with contextlib.suppress(Exception):
                conn.close()
    return info


def stop_lightpanda(container: str = LIGHTPANDA_CONTAINER) -> bool:
    """Stop the Lightpanda container. Returns True if docker rm reported success."""
    if not _wsl_available():
        return False
    proc = _run_wsl(
        ["wsl", "-e", "bash", "-lc", f"docker rm -f {container} 2>/dev/null"],
        timeout=20,
    )
    return proc.returncode == 0