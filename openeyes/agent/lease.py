"""The cross-process interaction lease.

Several agents may drive one desktop, but only one of them may hold the
pointer at a time. The lease is the agreement that makes that true, and
it has to hold across processes: a thread lock stops at the process
boundary, and a flag in memory stops at the machine boundary.

On Windows the lease is an exclusive ``CreateFileW`` open of a lock
file - no sharing at all. Windows itself refuses the second open while
the first is held, and it closes the handle when the process dies. So a
crashed agent leaves no stale lock behind, and a killed one cannot block
the desk forever. There is no cleanup to forget.

The lock path is derived from the user's SID, so two users on one
machine never contend with each other and one user's agent cannot
impose on another's.

Waiting is bounded: a caller that cannot get the lease in time is told
so with a structured code (``LEASE_TIMEOUT``) instead of blocking a
loop forever.
"""
from __future__ import annotations

import contextlib
import json
import os
import sys
import time
from typing import Any

from openeyes.agent.errors import LEASE_TIMEOUT

#: The lock file inside the per-user directory.
LOCK_FILE_NAME = "interaction.lock"

#: How often a waiting caller looks at the lock again.
DEFAULT_POLL_S = 0.05


class LeaseTimeout(Exception):
    """The lease was not free in time.

    ``error_code`` is the structured code an agent routes on.
    """

    error_code = LEASE_TIMEOUT

    def __init__(self, path: str, timeout_s: float) -> None:
        super().__init__(f"the interaction lease at {path} was not free "
                         f"within {timeout_s:g}s")
        self.path = path
        self.timeout_s = timeout_s


def lease_root() -> str:
    """The directory the locks live in."""
    base = (os.environ.get("LOCALAPPDATA")
            or os.environ.get("XDG_RUNTIME_DIR")
            or os.path.join(os.path.expanduser("~"), ".openeyes"))
    return os.path.join(base, "openeyes", "leases")


def _user_sid() -> str:
    """A per-user key: the Windows SID, or the user name elsewhere."""
    if sys.platform.startswith("win"):
        try:
            import win32api
            import win32con
            import win32security
            token = win32security.OpenProcessToken(
                win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
            sid, _attrs = win32security.GetTokenInformation(
                token, win32security.TokenUser)
            return win32security.ConvertSidToStringSid(sid)
        except Exception:
            return "default"
    try:
        return os.environ.get("USER") or str(os.getuid())
    except Exception:
        return "default"


def lease_path(root: str | None = None) -> str:
    """The lock file path for the current user."""
    return os.path.join(root or lease_root(), _user_sid(), LOCK_FILE_NAME)


def _holder_payload(agent_id: str) -> bytes:
    return json.dumps({"pid": os.getpid(), "agent_id": agent_id,
                       "since": time.time()}).encode("utf-8")


def _winerror(exc: Exception) -> int:
    code = getattr(exc, "winerror", None)
    if code is None:
        args = getattr(exc, "args", ())
        code = args[0] if args else 0
    try:
        return int(code)
    except (TypeError, ValueError):
        return 0


def _try_open_exclusive(path: str) -> Any:
    """Open the lock file exclusively, or return None when it is held."""
    if sys.platform.startswith("win"):
        import pywintypes
        import win32con
        import win32file
        try:
            return win32file.CreateFile(
                path,
                win32con.GENERIC_READ | win32con.GENERIC_WRITE,
                0,          # dwShareMode: no sharing at all - this is the lock
                None,       # security attributes
                win32con.OPEN_ALWAYS,
                win32con.FILE_ATTRIBUTE_NORMAL,
                None,
            )
        except pywintypes.error as exc:
            # 32 = ERROR_SHARING_VIOLATION, 33 = ERROR_LOCK_VIOLATION:
            # somebody else holds the lease.
            if _winerror(exc) in (32, 33):
                return None
            raise
    return _try_open_exclusive_posix(path)


def _try_open_exclusive_posix(path: str) -> Any:
    """The POSIX fallback: an exclusive create, with stale-holder cleanup."""
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
    except FileExistsError:
        if not _holder_is_gone(path):
            return None
        try:
            os.unlink(path)
        except OSError:
            return None
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        except OSError:
            return None
    return fd


def _holder_is_gone(path: str) -> bool:
    """Is the process that wrote this lock file no longer running?"""
    try:
        with open(path, "rb") as handle:
            payload = json.loads(handle.read().decode("utf-8"))
        pid = int(payload.get("pid", -1))
    except Exception:
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    except OSError:
        return False
    return False


def _write_holder(handle: Any, agent_id: str) -> None:
    """Note who holds the lease - diagnostics, never the lock itself."""
    payload = _holder_payload(agent_id)
    # The lock is the handle, not the note. Failing to write it is not a
    # reason to give the lease up.
    with contextlib.suppress(Exception):
        if sys.platform.startswith("win"):
            import win32file
            win32file.WriteFile(handle, payload)
        else:
            os.write(handle, payload)


def _close(handle: Any) -> None:
    try:
        handle.Close()
        return
    except AttributeError:
        pass
    with contextlib.suppress(OSError):
        os.close(handle)


class InteractionLease:
    """An exclusive claim on the physical input of one desktop.

    The lease is a handle, not a flag: whoever holds it may move the
    pointer, and nobody else may until it is released.
    """

    def __init__(self, *, root: str | None = None, timeout_s: float = 5.0,
                 poll_s: float = DEFAULT_POLL_S, agent_id: str = "default") -> None:
        self.path = lease_path(root)
        self.timeout_s = timeout_s
        self.poll_s = poll_s
        self.agent_id = agent_id
        #: is the lease held right now?
        self.held = False
        #: how many times the lock was found busy
        self.contention = 0
        #: the structured code, when the lease was not obtained
        self.error_code: str | None = None
        self._handle: Any = None

    def acquire(self) -> bool:
        """Take the lease, waiting up to the timeout.

        False means the lease is held elsewhere; ``error_code`` says so
        in a form a caller can route on.
        """
        deadline = time.monotonic() + self.timeout_s
        with contextlib.suppress(OSError):
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
        while True:
            handle = _try_open_exclusive(self.path)
            if handle is not None:
                self._handle = handle
                self.held = True
                self.error_code = None
                _write_holder(handle, self.agent_id)
                return True
            self.contention += 1
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.error_code = LEASE_TIMEOUT
                return False
            time.sleep(min(self.poll_s, remaining))

    def release(self) -> None:
        """Give the lease back. Safe to call more than once."""
        handle = self._handle
        self._handle = None
        self.held = False
        if handle is None:
            return
        _close(handle)

    def __enter__(self) -> InteractionLease:
        if not self.acquire():
            raise LeaseTimeout(self.path, self.timeout_s)
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.release()

    def __del__(self) -> None:
        with contextlib.suppress(Exception):
            self.release()

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "agent_id": self.agent_id,
            "held": self.held,
            "contention": self.contention,
            "error_code": self.error_code,
        }
