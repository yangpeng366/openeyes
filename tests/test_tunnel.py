"""Tests for the SSH forward helper (openeyes.core.tunnel)."""
from __future__ import annotations

import socket
import sys

import pytest

from openeyes.core.tunnel import SshTunnel, TunnelError, build_ssh_command, port_is_open


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_forward_spec_binds_loopback_only():
    tunnel = SshTunnel("yp-x240", local_port=19222, remote_port=9222)
    assert tunnel.forward_spec == "19222:127.0.0.1:9222"


def test_build_command_shape():
    cmd = build_ssh_command(
        "yp-x240",
        19222,
        9222,
        identity_file="C:/keys/id_ed25519",
        ssh_config="C:/ssh/config_x240",
    )
    assert cmd[0] == "ssh"
    assert "-N" in cmd
    assert "ExitOnForwardFailure=yes" in cmd
    assert cmd[cmd.index("-L") + 1] == "19222:127.0.0.1:9222"
    assert cmd[cmd.index("-F") + 1] == "C:/ssh/config_x240"
    assert cmd[cmd.index("-i") + 1] == "C:/keys/id_ed25519"
    assert cmd[-1] == "yp-x240"


def test_port_is_open_is_false_for_a_closed_port():
    assert port_is_open("127.0.0.1", _free_port()) is False


def test_start_fails_when_the_ssh_binary_is_missing():
    tunnel = SshTunnel("yp-x240", ssh_bin="openeyes-no-such-ssh-binary")
    with pytest.raises(TunnelError) as excinfo:
        tunnel.start(timeout=2.0)
    assert "openeyes-no-such-ssh-binary" in str(excinfo.value)


class _FakeSsh(SshTunnel):
    """An ssh stand-in whose behaviour is scripted by the test."""

    def __init__(self, script: str, **kwargs):
        super().__init__("fake", **kwargs)
        self._script = script

    def build_command(self):
        return [sys.executable, "-c", self._script]


def test_start_reports_an_early_exit_with_its_stderr():
    tunnel = _FakeSsh("import sys; sys.stderr.write('permission denied\\n'); sys.exit(255)")
    with pytest.raises(TunnelError) as excinfo:
        tunnel.start(timeout=5.0)
    message = str(excinfo.value)
    assert "exited with code 255" in message
    assert "permission denied" in message


def test_start_times_out_when_the_port_never_opens():
    tunnel = _FakeSsh("import time; time.sleep(30)", local_port=_free_port())
    with pytest.raises(TunnelError) as excinfo:
        tunnel.start(timeout=1.0)
    assert "did not open local port" in str(excinfo.value)


def test_stop_is_safe_to_call_twice():
    tunnel = SshTunnel("yp-x240")
    tunnel.stop()
    tunnel.stop()


def test_ssh_bin_override_replaces_only_the_program():
    tunnel = SshTunnel("yp-x240", ssh_bin="/usr/bin/ssh")
    cmd = tunnel.build_command()
    assert cmd[0] == "/usr/bin/ssh"
    assert cmd[-1] == "yp-x240"
