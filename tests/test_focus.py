"""Explicit focus has to prove the foreground, not trust a bool.

The primitive under test is best-effort: it can return False after a focus
switch that actually worked, or return True a moment before another window
takes the foreground back. These tests pin the report to the observed
foreground and keep the dry-run run free of side effects.
"""
from __future__ import annotations

import json
import os
import sys

import pytest
from click.testing import CliRunner

from openeyes.cli.main import cli
from openeyes.core.focus import (
    ERROR_BACKEND_ERROR,
    ERROR_FOCUS_OCCLUDED,
    ERROR_FOCUS_STOLEN,
    ERROR_FOREGROUND_NOT_ACQUIRED,
    ERROR_WINDOW_GEOMETRY_INVALID,
    ERROR_WINDOW_IDENTITY_CHANGED,
    ERROR_WINDOW_MINIMIZED,
    ERROR_WINDOW_NOT_VISIBLE,
    focus_window_checked,
)


def _probe(**overrides):
    info = {
        "valid": True,
        "minimized": False,
        "visible": True,
        "geometry_ok": True,
        "rect": {"left": 10, "top": 10, "right": 410, "bottom": 310},
        "detail": "",
        "title": "Untitled - Notepad",
        "class_name": "Notepad",
        "pid": 77,
    }
    info.update(overrides)
    return info


def _never_called(*_args, **_kwargs):
    raise AssertionError("the focus primitive must not be called")


def test_dry_run_reports_state_without_calling_the_primitive():
    calls = []

    def focuser(hwnd):
        calls.append(hwnd)
        return True

    report = focus_window_checked(
        42,
        dry_run=True,
        focuser=focuser,
        foreground=lambda: 99,
        probe=lambda _hwnd: _probe(),
        dpi=lambda: "per-monitor-v2",
    )

    assert report["ok"] is True
    assert report["dry_run"] is True
    assert report["reason"] == "dry-run"
    assert report["primitive_called"] is False
    assert report["primitive_result"] is None
    assert calls == []
    assert report["foreground_before"] == 99
    assert report["foreground_after"] == 99
    assert report["focused"] is False
    assert report["error"] is None


def test_success_requires_the_foreground_to_be_the_target_afterwards():
    state = {"foreground": 99}

    def focuser(hwnd):
        state["foreground"] = hwnd
        return True

    report = focus_window_checked(
        42,
        dry_run=False,
        focuser=focuser,
        foreground=lambda: state["foreground"],
        probe=lambda _hwnd: _probe(),
        dpi=lambda: "per-monitor-v2",
    )

    assert report["ok"] is True
    assert report["focused"] is True
    assert report["reason"] == "focused"
    assert report["primitive_called"] is True
    assert report["primitive_result"] is True
    assert report["foreground_before"] == 99
    assert report["foreground_after"] == 42
    assert report["error"] is None


def test_setforegroundwindow_failure_is_a_structured_failure():
    report = focus_window_checked(
        42,
        dry_run=False,
        focuser=lambda _hwnd: False,
        foreground=lambda: 99,
        probe=lambda _hwnd: _probe(),
        dpi=lambda: "per-monitor-v2",
    )

    assert report["ok"] is False
    assert report["focused"] is False
    assert report["error_code"] == ERROR_FOREGROUND_NOT_ACQUIRED
    assert report["foreground_before"] == 99
    assert report["foreground_after"] == 99
    assert "SetForegroundWindow" in report["error"]


def test_a_foreground_stolen_after_the_call_is_not_success():
    state = {"foreground": 99}

    def focuser(hwnd):
        state["foreground"] = hwnd
        return True

    def thief(hwnd):
        state["foreground"] = 777
        return True

    def focus_then_steal(hwnd):
        focuser(hwnd)
        return thief(hwnd)

    report = focus_window_checked(
        42,
        dry_run=False,
        focuser=focus_then_steal,
        foreground=lambda: state["foreground"],
        probe=lambda _hwnd: _probe(),
        dpi=lambda: "per-monitor-v2",
    )

    assert report["ok"] is False
    assert report["focused"] is False
    assert report["error_code"] == ERROR_FOCUS_STOLEN
    assert report["foreground_after"] == 777
    assert "stolen" in report["error"]


def test_a_minimised_window_fails_before_the_primitive_can_click():
    report = focus_window_checked(
        42,
        dry_run=False,
        focuser=_never_called,
        foreground=lambda: 99,
        probe=lambda _hwnd: _probe(minimized=True),
        dpi=lambda: "per-monitor-v2",
    )

    assert report["ok"] is False
    assert report["error_code"] == ERROR_WINDOW_MINIMIZED
    assert report["primitive_called"] is False
    assert report["focused"] is False


def test_an_already_focused_window_does_not_call_the_primitive():
    report = focus_window_checked(
        42,
        dry_run=False,
        focuser=_never_called,
        foreground=lambda: 42,
        probe=lambda _hwnd: _probe(pid=os.getpid()),
        dpi=lambda: "per-monitor-v2",
    )

    assert report["ok"] is True
    assert report["focused"] is True
    assert report["reason"] == "already-foreground"
    assert report["primitive_called"] is False
    assert report["same_process"] is True


def test_cli_focus_dry_run_is_json_and_has_no_side_effect(monkeypatch):
    monkeypatch.setattr("openeyes.core.focus._foreground_hwnd", lambda: 111)
    monkeypatch.setattr("openeyes.core.focus._probe_window",
                        lambda _hwnd: _probe())
    monkeypatch.setattr("openeyes.core.focus._default_dpi",
                        lambda: "per-monitor-v2")
    monkeypatch.setattr("openeyes.core.focus._focus_with_existing_primitive",
                        _never_called)

    result = CliRunner().invoke(cli, ["focus", "--window", "42", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["dry_run"] is True
    assert payload["hwnd"] == 42
    assert payload["foreground_before"] == 111
    assert payload["foreground_after"] == 111
    assert payload["focused"] is False
    assert payload["primitive_called"] is False


def test_cli_focus_go_reports_the_verified_foreground(monkeypatch):
    state = {"foreground": 111}
    monkeypatch.setattr("openeyes.core.focus._foreground_hwnd",
                        lambda: state["foreground"])

    def focuser(hwnd):
        state["foreground"] = hwnd
        return True

    monkeypatch.setattr("openeyes.core.focus._focus_with_existing_primitive",
                        focuser)
    monkeypatch.setattr("openeyes.core.focus._probe_window",
                        lambda _hwnd: _probe())
    monkeypatch.setattr("openeyes.core.focus._default_dpi",
                        lambda: "per-monitor-v2")

    result = CliRunner().invoke(
        cli, ["focus", "--window", "42", "--go", "--json"]
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["focused"] is True
    assert payload["foreground_before"] == 111
    assert payload["foreground_after"] == 42


def test_cli_focus_failure_exits_5_with_the_reason(monkeypatch):
    monkeypatch.setattr("openeyes.core.focus._foreground_hwnd", lambda: 111)
    monkeypatch.setattr("openeyes.core.focus._focus_with_existing_primitive",
                        lambda _hwnd: False)
    monkeypatch.setattr("openeyes.core.focus._probe_window",
                        lambda _hwnd: _probe())
    monkeypatch.setattr("openeyes.core.focus._default_dpi",
                        lambda: "per-monitor-v2")

    result = CliRunner().invoke(
        cli, ["focus", "--window", "42", "--go", "--json"]
    )

    assert result.exit_code == 5
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["focused"] is False
    assert payload["error_code"] == ERROR_FOREGROUND_NOT_ACQUIRED


def _noop_sleep(_seconds):
    """Keep the settle loop instant in tests; production still uses time.sleep."""
    return None


def test_failed_verification_read_leaves_after_unknown():
    state = {"n": 0, "focuser": 0}

    def foreground():
        state["n"] += 1
        if state["n"] == 1:
            return 99  # the before-read succeeds
        raise RuntimeError("foreground backend read failed")

    def focuser(_hwnd):
        state["focuser"] += 1
        return True

    report = focus_window_checked(
        42,
        dry_run=False,
        focuser=focuser,
        foreground=foreground,
        probe=lambda _hwnd: _probe(),
        dpi=lambda: "per-monitor-v2",
        sleep=_noop_sleep,
    )

    assert report["ok"] is False
    assert report["error_code"] == ERROR_BACKEND_ERROR
    assert report["foreground_before"] == 99
    assert report["foreground_after"] is None  # never fabricated from `before`
    assert report["focused"] is False
    assert report["primitive_called"] is True  # the report still says we tried
    assert report["primitive_result"] is True
    assert state["n"] > 2                      # read was retried, still unknown
    assert state["focuser"] == 1               # retry never re-activated


def test_focuser_exception_leaves_after_unknown():
    def focuser(_hwnd):
        raise RuntimeError("primitive blew up")

    report = focus_window_checked(
        42,
        dry_run=False,
        focuser=focuser,
        foreground=lambda: 99,
        probe=lambda _hwnd: _probe(),
        dpi=lambda: "per-monitor-v2",
        sleep=_noop_sleep,
    )

    assert report["ok"] is False
    assert report["error_code"] == ERROR_BACKEND_ERROR
    assert report["foreground_after"] is None  # call result unknown -> after unknown
    assert report["primitive_called"] is True
    # A raised primitive means "unknown", not "returned failure" (False).
    assert report["primitive_result"] is None


def test_window_identity_change_refuses_before_activation():
    state = {"probe": 0, "focuser": 0}

    def probe(_hwnd):
        state["probe"] += 1
        if state["probe"] == 1:
            return _probe(pid=77, title="Original", class_name="Notepad")
        # the hwnd was recycled / the window changed between probe and --go
        return _probe(pid=88, title="Different", class_name="Other")

    def focuser(_hwnd):
        state["focuser"] += 1
        return True

    report = focus_window_checked(
        42,
        dry_run=False,
        focuser=focuser,
        foreground=lambda: 99,
        probe=probe,
        dpi=lambda: "per-monitor-v2",
        sleep=_noop_sleep,
    )

    assert report["ok"] is False
    assert report["error_code"] == ERROR_WINDOW_IDENTITY_CHANGED
    assert report["primitive_called"] is False
    assert report["primitive_result"] is None
    assert report["foreground_after"] == 99  # no activation, no bogus claim
    assert state["probe"] == 2                # identity re-read before --go
    assert state["focuser"] == 0              # stale hwnd never got a title-bar click


def test_window_gone_at_recheck_is_not_activated():
    state = {"probe": 0, "focuser": 0}

    def probe(_hwnd):
        state["probe"] += 1
        if state["probe"] == 1:
            return _probe()
        return {"valid": False, "detail": "window handle is no longer valid"}

    def focuser(_hwnd):
        state["focuser"] += 1
        return True

    report = focus_window_checked(
        42,
        dry_run=False,
        focuser=focuser,
        foreground=lambda: 99,
        probe=probe,
        dpi=lambda: "per-monitor-v2",
        sleep=_noop_sleep,
    )

    assert report["ok"] is False
    assert report["error_code"] == ERROR_WINDOW_IDENTITY_CHANGED
    assert report["primitive_called"] is False
    assert state["focuser"] == 0  # the IsWindow recheck stopped activation


def test_settle_retry_succeeds_on_a_later_read():
    state = {"n": 0, "focuser": 0}

    def foreground():
        state["n"] += 1
        # before = 99; first settle read still 99 (slow async switch);
        # the second settle read finally sees the target
        return 99 if state["n"] < 3 else 42

    def focuser(_hwnd):
        state["focuser"] += 1
        return True

    report = focus_window_checked(
        42,
        dry_run=False,
        focuser=focuser,
        foreground=foreground,
        probe=lambda _hwnd: _probe(),
        dpi=lambda: "per-monitor-v2",
        sleep=_noop_sleep,
    )

    assert report["ok"] is True
    assert report["reason"] == "focused"
    assert report["focused"] is True
    assert report["foreground_after"] == 42
    assert state["n"] >= 3        # at least one settle retry happened
    assert state["focuser"] == 1  # retry used reads only, no re-activation


def test_transient_foreground_is_not_misjudged_as_stolen():
    state = {"n": 0}

    def foreground():
        state["n"] += 1
        # a different window flashes during the switch, then the target wins
        return {1: 99, 2: 555}.get(state["n"], 42)

    report = focus_window_checked(
        42,
        dry_run=False,
        focuser=lambda _hwnd: True,
        foreground=foreground,
        probe=lambda _hwnd: _probe(),
        dpi=lambda: "per-monitor-v2",
        sleep=_noop_sleep,
    )

    assert report["ok"] is True
    assert report["error_code"] is None
    assert report["foreground_after"] == 42  # FOCUS_STOLEN judged after settle


def test_settle_exhausted_reports_foreground_not_acquired():
    state = {"n": 0, "focuser": 0}

    def foreground():
        state["n"] += 1
        return 99  # never becomes the target

    def focuser(_hwnd):
        state["focuser"] += 1
        return True

    report = focus_window_checked(
        42,
        dry_run=False,
        focuser=focuser,
        foreground=foreground,
        probe=lambda _hwnd: _probe(),
        dpi=lambda: "per-monitor-v2",
        sleep=_noop_sleep,
    )

    assert report["ok"] is False
    assert report["error_code"] == ERROR_FOREGROUND_NOT_ACQUIRED
    assert report["foreground_after"] == 99
    assert report["focused"] is False
    assert state["n"] > 2        # the settle budget was actually used
    assert state["focuser"] == 1  # no input / clicks during the retry


def test_cli_focus_identity_change_exits_5_with_the_reason(monkeypatch):
    # The new structured error must flow through main.py's exit-code mapping
    # without changing it (WINDOW_IDENTITY_CHANGED -> the default failure 5).
    state = {"n": 0}
    monkeypatch.setattr("openeyes.core.focus._foreground_hwnd", lambda: 111)

    def probe(_hwnd):
        state["n"] += 1
        if state["n"] == 1:
            return _probe()
        return _probe(pid=999, title="Recycled", class_name="Other")

    monkeypatch.setattr("openeyes.core.focus._probe_window", probe)
    monkeypatch.setattr("openeyes.core.focus._default_dpi",
                        lambda: "per-monitor-v2")
    monkeypatch.setattr("openeyes.core.focus._focus_with_existing_primitive",
                        _never_called)

    result = CliRunner().invoke(
        cli, ["focus", "--window", "42", "--go", "--json"])

    assert result.exit_code == 5
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["error_code"] == ERROR_WINDOW_IDENTITY_CHANGED
    assert payload["primitive_called"] is False


@pytest.mark.skipif(sys.platform != "win32", reason="Windows backend")
def test_probe_window_reports_the_real_process_id():
    import win32gui
    import win32process

    from openeyes.core.focus import _probe_window

    hwnd = int(win32gui.GetForegroundWindow())
    if not hwnd:
        pytest.skip("no foreground window")
    expected = int(win32process.GetWindowThreadProcessId(hwnd)[1])
    info = _probe_window(hwnd)
    assert info["valid"] is True
    assert info["pid"] == expected


# ----- fix 1: a raised primitive is "unknown", not a known False -------------


def test_raised_primitive_is_unknown_not_false_and_json_keeps_null_distinct():
    def raiser(_hwnd):
        raise RuntimeError("primitive blew up")

    raised = focus_window_checked(
        42,
        dry_run=False,
        focuser=raiser,
        foreground=lambda: 99,
        probe=lambda _hwnd: _probe(),
        dpi=lambda: "per-monitor-v2",
        sleep=_noop_sleep,
    )
    assert raised["primitive_result"] is None       # unknown, not "returned failure"
    assert "raised" in raised["error"]             # the raised detail is preserved
    assert "primitive blew up" in raised["error"]

    returned = focus_window_checked(
        42,
        dry_run=False,
        focuser=lambda _hwnd: False,               # ran and returned a real bool
        foreground=lambda: 99,
        probe=lambda _hwnd: _probe(),
        dpi=lambda: "per-monitor-v2",
        sleep=_noop_sleep,
    )
    assert returned["primitive_result"] is False

    # JSON must keep null (unknown) distinct from false (known failure).
    assert json.loads(json.dumps(raised))["primitive_result"] is None
    assert json.loads(json.dumps(returned))["primitive_result"] is False


# ----- fix 2: the --go re-check reuses the round-1 rejection criteria ------


@pytest.mark.parametrize("bad_state,error_code", [
    ({"minimized": True}, ERROR_WINDOW_MINIMIZED),
    ({"visible": False}, ERROR_WINDOW_NOT_VISIBLE),
    ({"geometry_ok": False, "detail": "off-screen"}, ERROR_WINDOW_GEOMETRY_INVALID),
])
def test_second_probe_reverifies_operability(bad_state, error_code):
    state = {"probe": 0, "focuser": 0}

    def probe(_hwnd):
        state["probe"] += 1
        if state["probe"] == 1:
            return _probe()                 # first read: healthy
        return _probe(**bad_state)          # --go re-read: now unsafe

    def focuser(_hwnd):
        state["focuser"] += 1
        return True

    report = focus_window_checked(
        42,
        dry_run=False,
        focuser=focuser,
        foreground=lambda: 99,
        probe=probe,
        dpi=lambda: "per-monitor-v2",
        sleep=_noop_sleep,
    )

    assert report["ok"] is False
    assert report["error_code"] == error_code
    assert report["primitive_called"] is False
    assert report["primitive_result"] is None
    assert report["foreground_after"] == 99   # no activation, no fabricated value
    assert state["probe"] == 2                # the fresh re-read really happened
    assert state["focuser"] == 0              # unsafe state never reached the primitive


# ----- fix 3: settle has a hard wall-clock deadline -------------------------


@pytest.mark.parametrize("steady,expected_code", [
    (99, ERROR_FOREGROUND_NOT_ACQUIRED),   # last known reading == before
    (777, ERROR_FOCUS_STOLEN),             # last known reading != before
])
def test_settle_stops_at_the_wall_clock_deadline(steady, expected_code):
    clock = {"t": 0.0, "reads": 0}

    def fake_clock():
        return clock["t"]

    def foreground():
        clock["reads"] += 1
        clock["t"] += 0.4                    # each read is slow: burns the 1s budget
        return 99 if clock["reads"] == 1 else steady  # before-read differs from settle

    report = focus_window_checked(
        42,
        dry_run=False,
        focuser=lambda _hwnd: True,
        foreground=foreground,
        probe=lambda _hwnd: _probe(),
        dpi=lambda: "per-monitor-v2",
        sleep=_noop_sleep,
        clock=fake_clock,
    )

    assert report["error_code"] == expected_code
    assert report["foreground_after"] == steady   # verdict uses the last known reading
    assert report["focused"] is False
    # Without a wall-clock deadline all 10 settle reads would have run
    # (1 before-read + 10 settle = 11); the clock stopped it early.
    assert clock["reads"] < 11


# ----- fix 4: the title-bar fallback must not click into another window -----


def test_occluded_primitive_reports_focus_occluded():
    class Refused(Exception):
        focus_error_code = ERROR_FOCUS_OCCLUDED

    def focuser(_hwnd):
        raise Refused("title-bar point belongs to hwnd=777")

    report = focus_window_checked(
        42,
        dry_run=False,
        focuser=focuser,
        foreground=lambda: 99,
        probe=lambda _hwnd: _probe(),
        dpi=lambda: "per-monitor-v2",
        sleep=_noop_sleep,
    )

    assert report["ok"] is False
    assert report["error_code"] == ERROR_FOCUS_OCCLUDED
    assert report["primitive_called"] is True
    assert report["primitive_result"] is None       # a refusal is still "unknown"
    assert report["foreground_after"] is None
    assert "occluded" in report["error"]
    assert "777" in report["error"]                 # the raised detail is preserved


def test_cli_focus_occluded_exits_5_with_the_new_code(monkeypatch):
    class Refused(Exception):
        focus_error_code = ERROR_FOCUS_OCCLUDED

    monkeypatch.setattr("openeyes.core.focus._foreground_hwnd", lambda: 111)
    monkeypatch.setattr("openeyes.core.focus._probe_window",
                        lambda _hwnd: _probe())
    monkeypatch.setattr("openeyes.core.focus._default_dpi",
                        lambda: "per-monitor-v2")

    def refuse(_hwnd):
        raise Refused("title-bar point belongs to another window")

    monkeypatch.setattr("openeyes.core.focus._focus_with_existing_primitive",
                        refuse)

    result = CliRunner().invoke(
        cli, ["focus", "--window", "42", "--go", "--json"])

    assert result.exit_code == 5
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["error_code"] == ERROR_FOCUS_OCCLUDED
    assert payload["primitive_result"] is None


@pytest.mark.skipif(sys.platform != "win32", reason="Windows backend")
@pytest.mark.parametrize("point_hwnd,ancestor_root,expect_click,expect_result", [
    (777, 777, False, False),   # occluded: point belongs to another window
    (42, 42, True, True),       # point is the target itself
    (555, 42, True, True),      # point is a child whose GA_ROOT is the target
])
def test_focus_window_title_bar_fallback_guards_occlusion(
        monkeypatch, point_hwnd, ancestor_root, expect_click, expect_result):
    import types

    import openeyes.actuators.win32 as win32

    hwnd = 42
    clicks = []

    class _FakeWrapper:
        def set_focus(self):
            return None

    class _FakeDesktop:
        def __init__(self, backend=None):
            pass

        def window(self, handle=None):
            return _FakeWrapper()

    fg = iter([999, hwnd])       # not foreground first, target after a click
    fake_gui = types.SimpleNamespace(
        GetForegroundWindow=lambda: next(fg),
        GetWindowRect=lambda _h: (10, 10, 410, 310),
        WindowFromPoint=lambda _pt: point_hwnd,
        GetAncestor=lambda _h, _flag: ancestor_root,
    )
    monkeypatch.setattr(win32, "Desktop", _FakeDesktop)
    monkeypatch.setattr(win32, "win32gui", fake_gui)
    monkeypatch.setattr(win32, "click_xy",
                        lambda x, y, *a, **k: clicks.append((x, y)))
    monkeypatch.setattr(win32, "time",
                        types.SimpleNamespace(sleep=lambda _s: None))

    assert win32.focus_window(hwnd) is expect_result
    assert clicks == ([(210, 30)] if expect_click else [])


@pytest.mark.skipif(sys.platform != "win32", reason="Windows backend")
def test_occluded_title_bar_raises_a_structured_refusal_for_the_core(monkeypatch):
    import types

    import openeyes.actuators.win32 as win32

    hwnd = 42
    clicks = []

    class _FakeWrapper:
        def set_focus(self):
            return None

    class _FakeDesktop:
        def __init__(self, backend=None):
            pass

        def window(self, handle=None):
            return _FakeWrapper()

    fake_gui = types.SimpleNamespace(
        GetForegroundWindow=lambda: 999,       # never becomes the target
        GetWindowRect=lambda _h: (10, 10, 410, 310),
        WindowFromPoint=lambda _pt: 777,      # the occluding window
        GetAncestor=lambda _h, _flag: 777,    # its root is itself, not the target
    )
    monkeypatch.setattr(win32, "Desktop", _FakeDesktop)
    monkeypatch.setattr(win32, "win32gui", fake_gui)
    monkeypatch.setattr(win32, "click_xy",
                        lambda x, y, *a, **k: clicks.append((x, y)))
    monkeypatch.setattr(win32, "time",
                        types.SimpleNamespace(sleep=lambda _s: None))

    # The core layer calls the impl directly to see the structured refusal.
    with pytest.raises(win32.FocusRefused) as excinfo:
        win32._focus_window_impl(hwnd)
    assert excinfo.value.focus_error_code == win32.FOCUS_OCCLUDED
    assert clicks == []

    # The public bool entry point turns that same refusal into False.
    assert win32.focus_window(hwnd) is False
    assert clicks == []

def test_restore_unminimises_before_focusing():
    # probe #1 sees the minimised window, #2 confirms the restore, #3 is the
    # pre-activation identity re-check.
    probes = [_probe(minimized=True), _probe(), _probe()]
    state = {"foreground": 99}
    restored = []

    def focuser(hwnd):
        state["foreground"] = hwnd
        return True

    report = focus_window_checked(
        42,
        dry_run=False,
        restore=True,
        restorer=lambda hwnd: restored.append(hwnd),
        focuser=focuser,
        foreground=lambda: state["foreground"],
        probe=lambda _hwnd: probes.pop(0),
        dpi=lambda: "per-monitor-v2",
        sleep=lambda _s: None,
    )

    assert restored == [42]
    assert report["restored"] is True
    assert report["ok"] is True
    assert report["focused"] is True
    assert report["foreground_after"] == 42


def test_restore_is_opt_in():
    report = focus_window_checked(
        42,
        dry_run=False,
        restorer=_never_called,
        focuser=_never_called,
        foreground=lambda: 99,
        probe=lambda _hwnd: _probe(minimized=True),
        dpi=lambda: "per-monitor-v2",
        sleep=lambda _s: None,
    )

    assert report["ok"] is False
    assert report["error_code"] == ERROR_WINDOW_MINIMIZED
    assert report["restored"] is False
