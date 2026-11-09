"""SSH local-forward tunnels for reaching a remote browser's debug port.

OpenEyes drives a browser over a debug port. When that browser lives on
another machine - an Ubuntu helper box reached over SSH - the port has to
travel through an SSH local forward first, and the remote browser usually
checks the HTTP Host header of the forwarded request. The forward is
therefore not just a pipe: it has to survive the hop and look local to the
browser.

This module owns that pipe. It starts ``ssh -N -L``, waits until the local
port actually accepts a connection, and fails with a message that names the
cause instead of leaving the caller with a dead port. Nothing here is
browser-specific; the BiDi backend consumes whatever port comes out.

The forward binds the local end to 127.0.0.1 only. A debug port is a
privileged interface into a browser session - cookies, tokens, a logged-in
identity - so it is never exposed on a routable address.
"""

from __future__ import annotations

import os
import socket
import subprocess
import time
from dataclasses import dataclass, field

__all__ = ["SshTunnel", "TunnelError", "build_ssh_command", "port_is_open"]


class TunnelError(RuntimeError):
    """Raised when a tunnel cannot be established or dies early."""


def port_is_open(host: str = "127.0.0.1", port: int = 9222, timeout: float = 0.5) -> bool:
    """Return True when something accepts a TCP connection on host:port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        return sock.connect_ex((host, port)) == 0


def build_ssh_command(
    ssh_host: str,
    local_port: int,
    remote_port: int,
    *,
    remote_bind: str = "127.0.0.1",
    identity_file: str | None = None,
    ssh_config: str | None = None,
    connect_timeout: int = 15,
    extra_args: list[str] | None = None,
) -> list[str]:
    """Build the ``ssh`` argv for a local forward of remote_port to local_port.

    ``ExitOnForwardFailure=yes`` is not optional: without it ssh keeps running
    after the forward failed to bind, and the caller stares at a local port
    that never opens while the real cause scrolls past in the log.
    """
    cmd = [
        "ssh",
        "-N",
        "-o",
        "ExitOnForwardFailure=yes",
        "-o",
        f"ConnectTimeout={connect_timeout}",
        "-o",
        "ServerAliveInterval=30",
        "-o",
        "ServerAliveCountMax=3",
        "-L",
        f"{local_port}:{remote_bind}:{remote_port}",
    ]
    if ssh_config:
        cmd += ["-F", ssh_config]
    if identity_file:
        cmd += ["-i", identity_file]
    cmd += list(extra_args or [])
    cmd.append(ssh_host)
    return cmd


@dataclass
class SshTunnel:
    """An SSH local forward, owned as a context manager.

    >>> with SshTunnel("yp-x240", local_port=19222, remote_port=9222) as t:
    ...     port_is_open("127.0.0.1", t.local_port)   # doctest: +SKIP
    """

    ssh_host: str
    local_port: int = 19222
    remote_port: int = 9222
    remote_bind: str = "127.0.0.1"
    identity_file: str | None = None
    ssh_config: str | None = None
    connect_timeout: int = 15
    ssh_bin: str = "ssh"
    extra_args: list[str] = field(default_factory=list)
    process: subprocess.Popen | None = field(default=None, init=False, repr=False)

    @property
    def forward_spec(self) -> str:
        return f"{self.local_port}:{self.remote_bind}:{self.remote_port}"

    def build_command(self) -> list[str]:
        cmd = build_ssh_command(
            self.ssh_host,
            self.local_port,
            self.remote_port,
            remote_bind=self.remote_bind,
            identity_file=self.identity_file,
            ssh_config=self.ssh_config,
            connect_timeout=self.connect_timeout,
            extra_args=list(self.extra_args),
        )
        if self.ssh_bin != "ssh":
            cmd[0] = self.ssh_bin
        return cmd

    def start(self, timeout: float = 12.0) -> SshTunnel:
        """Start the forward and block until the local port accepts traffic."""
        if self.process is not None and self.process.poll() is None:
            return self

        cmd = self.build_command()
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        try:
            self.process = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=creationflags,
            )
        except OSError as exc:
            raise TunnelError(f"cannot start {cmd[0]!r}: {exc}") from exc

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise TunnelError(self._early_exit_message(cmd))
            if port_is_open("127.0.0.1", self.local_port):
                # Give ssh a moment to die if the forward was refused because
                # the local port is already taken by somebody else: a port we
                # did not open must not look like a tunnel we did open.
                time.sleep(0.3)
                if self.process.poll() is not None:
                    raise TunnelError(self._early_exit_message(cmd))
                return self
            time.sleep(0.2)

        self.stop()
        raise TunnelError(
            f"forward {self.forward_spec} did not open local port {self.local_port} "
            f"within {timeout:g}s; command: {' '.join(cmd)}"
        )

    def stop(self) -> None:
        """Terminate the forward. Safe to call more than once."""
        proc, self.process = self.process, None
        if proc is None or proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    def _early_exit_message(self, cmd: list[str]) -> str:
        stderr = ""
        if self.process is not None and self.process.stderr is not None:
            try:
                stderr = self.process.stderr.read().decode("utf-8", "replace")
            except (OSError, ValueError):
                stderr = ""
        code = self.process.returncode if self.process is not None else "?"
        return (
            f"ssh exited with code {code} before the forward was ready; "
            f"command: {' '.join(cmd)}; stderr: {stderr.strip() or '(empty)'}"
        )

    def __enter__(self) -> SshTunnel:
        return self.start()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()
