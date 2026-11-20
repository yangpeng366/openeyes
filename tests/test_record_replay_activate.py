"""``--activate``: focus the target window before live delivery.

Live input lands on whatever is foreground, so a replay against a background
window delivers the click somewhere else. ``activate=True`` makes that a
checkable precondition: the step is only delivered once the target is observed
in front, and when focus cannot be taken nothing is sent.
"""
from __future__ import annotations

from openeyes.record import Recorder, Replayer


def _click_traj(steps: int = 1, meta=None, hwnd: int = 4242):
    recorder = Recorder()
    recorder.start(name="activate-test")
    element = {
        "name": "OK",
        "automation_id": "ok_button",
        "control_type": "Button",
        "center": {"x": 10, "y": 20},
    }
    for _ in range(steps):
        recorder.record_click(hwnd, element, intent="click ok", meta=meta)
    return recorder.stop()


class _Backend:
    """Records the order of calls; never touches mouse or keyboard."""

    def __init__(self, *, activate_ok: bool = True, present: bool = False,
                 error: str | None = None):
        self.activate_ok = activate_ok
        self.present = present
        self.error = error
        self.calls: list[tuple] = []

    def activate(self, hwnd):
        self.calls.append(("activate", hwnd))
        if self.error is not None:
            raise RuntimeError(self.error)
        return {"ok": self.activate_ok, "hwnd": hwnd,
                "error_code": None if self.activate_ok else "FOCUS_STOLEN",
                "error": None if self.activate_ok else "foreground went elsewhere"}

    def click_selector(self, hwnd, selector, *, button="left", double=False):
        self.calls.append(("click_selector", hwnd, selector))
        self.present = True

    def click_xy(self, x, y, *, button="left", double=False):
        self.calls.append(("click_xy", x, y))
        self.present = True

    def send_hotkey(self, combo):
        self.calls.append(("send_hotkey", combo))

    def type_text(self, text):
        self.calls.append(("type_text", text))

    def probe(self, spec, *, hwnd=0):
        return {"found": self.present, "observed": f"present={self.present}"}


def _replay(backend, trajectory, **kwargs):
    return Replayer(dry_run=False, backend=backend, timeout=0.1,
                    interval=0.01, **kwargs).replay(trajectory)


def test_activate_is_off_by_default():
    backend = _Backend()
    report = _replay(backend, _click_traj(meta={"expect": {"automation_id": "ok_button"}}))
    assert [c[0] for c in backend.calls] == ["click_selector"]
    assert report.actions[0]["activation"] is None
    assert report.actions[0]["status"] == "verified"


def test_activate_runs_before_delivery():
    backend = _Backend()
    report = _replay(
        backend,
        _click_traj(meta={"expect": {"automation_id": "ok_button"}}),
        activate=True,
    )
    assert [c[0] for c in backend.calls] == ["activate", "click_selector"]
    assert backend.calls[0] == ("activate", 4242)
    assert report.actions[0]["status"] == "verified"
    assert report.actions[0]["activation"]["ok"] is True


def test_activation_is_cached_per_hwnd():
    backend = _Backend()
    report = _replay(backend, _click_traj(steps=3), activate=True)
    assert [c[0] for c in backend.calls].count("activate") == 1
    assert [c[0] for c in backend.calls].count("click_selector") == 3
    assert all(a["activation"]["ok"] for a in report.actions)


def test_failed_activation_refuses_to_deliver():
    backend = _Backend(activate_ok=False)
    report = _replay(
        backend,
        _click_traj(meta={"expect": {"automation_id": "ok_button"}}),
        activate=True,
    )
    action = report.actions[0]
    assert action["status"] == "failed"
    assert action["sent"] is False
    assert "not activated" in action["reason"]
    assert [c[0] for c in backend.calls] == ["activate"]


def test_activation_exception_is_reported_not_raised():
    backend = _Backend(error="access denied")
    report = _replay(backend, _click_traj(), activate=True)
    action = report.actions[0]
    assert action["status"] == "failed"
    assert "access denied" in action["error"]
    assert action["sent"] is False


def test_backend_without_activate_refuses_instead_of_clicking_blindly():
    class _NoActivate(_Backend):
        activate = None

    backend = _NoActivate()
    report = _replay(backend, _click_traj(), activate=True)
    assert report.actions[0]["status"] == "failed"
    assert "activate()" in report.actions[0]["reason"]
    assert backend.calls == []


def test_refused_activation_is_counted_as_not_delivered():
    """A refused window is an undelivered step, not a delivered failure.

    Both steps share the hwnd, so both are refused by the cached activation:
    nothing was sent, and the summary must say so instead of leaving the two
    steps indistinguishable from ones that ran and missed their expectation.
    """
    backend = _Backend(activate_ok=False)
    report = _replay(
        backend,
        _click_traj(steps=2, meta={"expect": {"automation_id": "ok_button"}}),
        activate=True,
    )

    summary = report.to_dict()["summary"]
    assert summary["failed"] == 2
    assert summary["refused_activation"] == 2
    assert summary["delivered"] == 0
    assert summary["not_delivered"] == 2
    assert all(action["sent"] is False for action in report.actions)


def test_delivered_step_whose_effect_is_unproven_is_not_counted_as_undelivered():
    """The other side of the split: delivered, unverified, still delivered."""
    backend = _Backend()
    report = _replay(backend, _click_traj(), activate=True)

    summary = report.to_dict()["summary"]
    assert summary["verified"] == 0
    assert summary["unverified"] == 1
    assert summary["delivered"] == 1
    assert summary["not_delivered"] == 0
    assert summary["refused_activation"] == 0


def test_a_dry_run_does_not_claim_any_delivery():
    """Planned steps are neither delivered nor refused - both stay unclaimed."""
    report = Replayer(dry_run=True).replay(_click_traj())
    summary = report.to_dict()["summary"]

    assert summary["planned"] == 1
    assert summary["delivered"] is None
    assert summary["not_delivered"] is None
    assert summary["refused_activation"] == 0
