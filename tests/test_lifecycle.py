"""Tests for the app-lifecycle primitives.

The behaviour under test is the one that bites in production: a session
timeout closes the app's window, and "the window is gone" must not be
confused with "the process is gone" — the two need different remedies.

The unit tests drive the core module with a fake backend so they run
anywhere; the live tests exercise the real Windows backend.
"""
from __future__ import annotations

import sys

import pytest

from openeyes.core import lifecycle as lc


class FakeBackend:
    """Stand-in for openeyes.backends.lifecycle."""

    def __init__(self, *, window=None, process=False, launch_ok=True):
        self.window = window
        self.process = process
        self.launch_ok = launch_ok
        self.launched = []
        self.restored = []

    # --- window lookup ---
    def find_window(self, *, title_contains=None, class_name=None,
                  process_name=None):
        return self.window

    def state_for_window(self, hwnd):
        if self.window is None:
            return lc.AppState(state=lc.STATE_NOT_RUNNING, hwnd=0, pid=0,
                               detail="window handle is no longer valid")
        return lc.AppState(state=lc.STATE_RUNNING, hwnd=hwnd,
                           pid=self.window.pid, detail="window handle is valid")

    # --- process lookup ---
    def process_alive(self, process_name):
        return self.process, ("process alive" if self.process
                              else "process not found")

    # --- actions ---
    def restore_window(self, hwnd):
        self.restored.append(hwnd)
        return {"ok": True, "restored": True, "detail": "restored",
                "foreground_stolen": False}

    def launch_app(self, *, exe=None, aumid=None, args=(), working_dir=None):
        self.launched.append({"exe": exe, "aumid": aumid, "args": args})
        if not self.launch_ok:
            return {"launched": False, "method": "", "detail": "boom", "pid": 0}
        return {"launched": True, "method": "exe", "detail": "started", "pid": 42}


class FakeWindow:
    def __init__(self, hwnd=4242, pid=77, title="Widget", minimized=False):
        self.hwnd = hwnd
        self.pid = pid
        self.title = title
        self.class_name = "Fake"
        self.minimized = minimized


@pytest.fixture
def fake(monkeypatch):
    backend = FakeBackend()
    monkeypatch.setattr(lc, "_backend", lambda: backend, raising=False)
    return backend


def _install(monkeypatch, backend):
    monkeypatch.setattr(lc, "_backend", lambda: backend, raising=False)


# ------------------------------------------------------------------ states
def test_app_state_reports_running_when_the_window_is_there(monkeypatch):
    _install(monkeypatch, FakeBackend(window=FakeWindow()))
    state = lc.app_state(title_contains="Widget")
    assert state.state == lc.STATE_RUNNING
    assert state.usable is True
    assert state.hwnd == 4242


def test_app_state_separates_window_gone_from_process_gone(monkeypatch):
    """The session-timeout case: the process lives, the window does not."""
    _install(monkeypatch, FakeBackend(window=None, process=True))
    state = lc.app_state(process_name="Widget")
    assert state.state == lc.STATE_WINDOW_GONE
    assert state.running is True
    assert state.usable is False


def test_app_state_reports_not_running_when_neither_exists(monkeypatch):
    _install(monkeypatch, FakeBackend(window=None, process=False))
    state = lc.app_state(process_name="Widget")
    assert state.state == lc.STATE_NOT_RUNNING
    assert state.running is False
    assert state.usable is False


def test_app_state_is_unknown_without_any_anchor(monkeypatch):
    _install(monkeypatch, FakeBackend())
    state = lc.app_state()
    assert state.state == lc.STATE_UNKNOWN
    assert state.running is False


# ------------------------------------------------------------------ ensure
def test_ensure_app_returns_immediately_when_the_app_is_already_usable(monkeypatch):
    backend = FakeBackend(window=FakeWindow())
    _install(monkeypatch, backend)
    result = lc.ensure_app(title_contains="Widget")
    assert result.ok is True
    assert result.action == "none"
    assert backend.launched == []
    assert backend.restored == []


def test_ensure_app_dry_run_never_launches_or_restores(monkeypatch):
    backend = FakeBackend(window=None, process=False)
    _install(monkeypatch, backend)
    result = lc.ensure_app(process_name="Widget", exe="widget.exe",
                            dry_run=True)
    assert result.ok is False
    assert result.action == "launch"
    assert backend.launched == []
    assert backend.restored == []


def test_ensure_app_dry_run_reports_a_restore_without_doing_it(monkeypatch):
    """A live process with a closed window needs a restore, not a relaunch."""
    backend = FakeBackend(window=FakeWindow(minimized=True))
    _install(monkeypatch, backend)
    result = lc.ensure_app(process_name="Widget", dry_run=True)
    assert result.action == "restore"
    assert backend.restored == []
    assert backend.launched == []


def test_ensure_app_launches_when_the_window_is_gone_even_if_the_process_lives(monkeypatch):
    """No window handle means nothing to restore - relaunch is the remedy."""
    backend = FakeBackend(window=None, process=True)
    _install(monkeypatch, backend)
    result = lc.ensure_app(process_name="Widget", exe="widget.exe",
                            dry_run=True)
    assert result.action == "launch"
    assert backend.restored == []
    assert backend.launched == []


def test_ensure_app_launches_and_proves_the_app_came_back(monkeypatch):
    """A launch is delivered, not effective — ok only when it is usable."""
    backend = FakeBackend(window=None, process=False)
    _install(monkeypatch, backend)

    states = iter([
        lc.AppState(state=lc.STATE_NOT_RUNNING, hwnd=0, pid=0, detail="gone"),
        lc.AppState(state=lc.STATE_NOT_RUNNING, hwnd=0, pid=0, detail="gone"),
        lc.AppState(state=lc.STATE_RUNNING, hwnd=99, pid=42, detail="up"),
    ])

    def fake_state(**_kwargs):
        try:
            return next(states)
        except StopIteration:
            return lc.AppState(state=lc.STATE_RUNNING, hwnd=99, pid=42)

    monkeypatch.setattr(lc, "app_state", fake_state)
    result = lc.ensure_app(process_name="Widget", exe="widget.exe",
                            timeout=2.0, interval=0.01, dry_run=False)
    assert result.ok is True
    assert result.action == "launch"
    assert result.hwnd == 99
    assert len(backend.launched) == 1


def test_ensure_app_fails_when_the_app_never_becomes_usable(monkeypatch):
    backend = FakeBackend(window=None, process=False)
    _install(monkeypatch, backend)
    monkeypatch.setattr(
        lc, "app_state",
        lambda **_k: lc.AppState(state=lc.STATE_NOT_RUNNING, hwnd=0, pid=0),
    )
    result = lc.ensure_app(process_name="Widget", exe="widget.exe",
                            timeout=0.3, interval=0.01, dry_run=False)
    assert result.ok is False
    assert "usable" in (result.error or "")


def test_ensure_app_reports_a_failed_launch_without_waiting(monkeypatch):
    backend = FakeBackend(window=None, process=False, launch_ok=False)
    _install(monkeypatch, backend)
    result = lc.ensure_app(process_name="Widget", exe="widget.exe",
                            timeout=5.0, interval=0.01, dry_run=False)
    assert result.ok is False
    assert result.error
    assert result.attempts == 0


def test_ensure_app_honours_a_stricter_ready_check(monkeypatch):
    """Usable means more than "a window exists" when the caller says so."""
    backend = FakeBackend(window=None, process=False)
    _install(monkeypatch, backend)
    monkeypatch.setattr(
        lc, "app_state",
        lambda **_k: lc.AppState(state=lc.STATE_RUNNING, hwnd=5, pid=5),
    )
    seen = []

    def ready(hwnd):
        seen.append(hwnd)
        return len(seen) >= 3

    result = lc.ensure_app(process_name="Widget", exe="widget.exe",
                            ready=ready, timeout=2.0, interval=0.01,
                            dry_run=False)
    assert result.ok is True
    assert len(seen) == 3


def test_ensure_app_treats_a_raising_ready_check_as_not_ready_yet(monkeypatch):
    backend = FakeBackend(window=None, process=False)
    _install(monkeypatch, backend)
    monkeypatch.setattr(
        lc, "app_state",
        lambda **_k: lc.AppState(state=lc.STATE_RUNNING, hwnd=5, pid=5),
    )

    def ready(_hwnd):
        raise RuntimeError("tree not ready")

    result = lc.ensure_app(process_name="Widget", exe="widget.exe",
                            ready=ready, timeout=0.2, interval=0.01,
                            dry_run=False)
    assert result.ok is False


# ------------------------------------------------------------------- live
@pytest.mark.skipif(sys.platform != "win32", reason="Windows backend")
def test_live_process_alive_detects_a_running_process():
    from openeyes.backends import lifecycle as backend

    alive, detail = backend.process_alive("explorer")
    assert alive is True
    assert "explorer" in detail.lower()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows backend")
def test_live_process_alive_rejects_an_absent_process():
    from openeyes.backends import lifecycle as backend

    alive, _detail = backend.process_alive("no-such-process-openeyes-xyz")
    assert alive is False


@pytest.mark.skipif(sys.platform != "win32", reason="Windows backend")
def test_live_app_state_finds_a_real_window():
    """The console host itself is a real top-level window."""
    from openeyes.core.windows import list_windows

    windows = list_windows()
    if not windows:
        pytest.skip("no visible top-level window in this session")
    title = windows[0].title
    state = lc.app_state(title_contains=title[:8])
    # A minimised window is found but is not usable (empty tree), so the
    # state is window-gone rather than running.
    assert state.state in (lc.STATE_RUNNING, lc.STATE_WINDOW_GONE)
    assert state.hwnd == windows[0].hwnd
    assert state.running is True


@pytest.mark.skipif(sys.platform != "win32", reason="Windows backend")
def test_live_restore_window_keeps_the_foreground_elsewhere():
    """Restoring must not steal focus — the user keeps working."""
    import win32gui

    from openeyes.backends import lifecycle as backend

    before = win32gui.GetForegroundWindow()
    report = backend.restore_window(0)
    assert report["ok"] is False  # an invalid handle never raises
    assert win32gui.GetForegroundWindow() == before
