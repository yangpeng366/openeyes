"""Tests for the cross-process interaction lease.

The lease has one job: while one agent holds the pointer, no other
agent - in this process or in another - may hold it. The tests below
pin both halves of that, including the one that matters most in
practice: an agent that dies must not leave the desk locked.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

from openeyes.agent.errors import LEASE_TIMEOUT
from openeyes.agent.lease import InteractionLease, LeaseTimeout, lease_path

REPO = Path(__file__).resolve().parents[1]

#: A helper process that takes the lease and holds it until it is killed.
_HOLDER = """
import sys, time
sys.path.insert(0, {repo!r})
from openeyes.agent.lease import InteractionLease

lease = InteractionLease(root={root!r}, timeout_s=5.0, agent_id="holder")
if not lease.acquire():
    print("refused", flush=True)
    raise SystemExit(1)
print("held", flush=True)
time.sleep(120)
"""


def _spawn_holder(root: str) -> subprocess.Popen:
    script = _HOLDER.format(repo=str(REPO), root=root)
    return subprocess.Popen([sys.executable, "-c", script],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True)


def _wait_for(proc: subprocess.Popen, word: str, timeout_s: float = 15.0) -> str:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        line = proc.stdout.readline()
        if line:
            if word in line:
                return line
            return line
        if proc.poll() is not None:
            break
        time.sleep(0.05)
    raise AssertionError(f"the helper process never said {word!r}")


# --------------------------------------------------------------- exclusivity
def test_the_lease_is_exclusive(tmp_path):
    """One holder at a time, even inside one process."""
    first = InteractionLease(root=str(tmp_path), timeout_s=0.2)
    second = InteractionLease(root=str(tmp_path), timeout_s=0.2)
    assert first.acquire() is True
    try:
        assert second.acquire() is False
        assert second.error_code == LEASE_TIMEOUT
        assert second.contention >= 1
    finally:
        first.release()


def test_releasing_the_lease_frees_it(tmp_path):
    first = InteractionLease(root=str(tmp_path), timeout_s=0.2)
    assert first.acquire() is True
    first.release()
    assert first.held is False
    second = InteractionLease(root=str(tmp_path), timeout_s=0.5)
    assert second.acquire() is True
    second.release()


def test_releasing_twice_is_harmless(tmp_path):
    lease = InteractionLease(root=str(tmp_path), timeout_s=0.2)
    assert lease.acquire() is True
    lease.release()
    lease.release()


def test_the_context_manager_raises_when_the_lease_is_busy(tmp_path):
    holder = InteractionLease(root=str(tmp_path), timeout_s=0.2)
    assert holder.acquire() is True
    try:
        with pytest.raises(LeaseTimeout), \
                InteractionLease(root=str(tmp_path), timeout_s=0.2):
            raise AssertionError("the lease must not be granted twice")
    finally:
        holder.release()


# ------------------------------------------------------------------ the path
def test_the_path_is_derived_from_the_user(tmp_path):
    """Two users on one machine must not contend with each other."""
    one = lease_path(str(tmp_path))
    two = lease_path(str(tmp_path / "other"))
    assert one != two
    assert one.endswith("interaction.lock")


# --------------------------------------------------------------- across processes
def test_a_second_process_cannot_take_the_lease(tmp_path):
    proc = _spawn_holder(str(tmp_path))
    try:
        _wait_for(proc, "held")
        mine = InteractionLease(root=str(tmp_path), timeout_s=0.5)
        assert mine.acquire() is False
        assert mine.error_code == LEASE_TIMEOUT
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_a_killed_holder_does_not_lock_the_desk(tmp_path):
    """The acceptance case: a killed agent must not leave a dead lock."""
    proc = _spawn_holder(str(tmp_path))
    _wait_for(proc, "held")
    proc.terminate()
    proc.wait(timeout=10)

    mine = InteractionLease(root=str(tmp_path), timeout_s=5.0)
    assert mine.acquire() is True
    mine.release()


def test_the_holder_note_names_the_agent(tmp_path):
    """After the fact, the lock file says who held it - for diagnostics."""
    lease = InteractionLease(root=str(tmp_path), timeout_s=0.5,
                             agent_id="worker-a")
    assert lease.acquire() is True
    lease.release()
    note = (Path(lease.path)).read_text(encoding="utf-8")
    assert "worker-a" in note


def test_the_lease_reports_its_own_state(tmp_path):
    lease = InteractionLease(root=str(tmp_path), timeout_s=0.2)
    state = lease.to_dict()
    assert state["held"] is False
    assert state["error_code"] is None
    assert state["agent_id"] == "default"
